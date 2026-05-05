import subprocess
import os
import json


def get_info(path):
    cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json',
           '-show_streams', '-show_format', path]
    data = json.loads(subprocess.check_output(cmd).decode('utf-8'))
    v = next(s for s in data['streams'] if s['codec_type'] == 'video')
    has_audio = any(s['codec_type'] == 'audio' for s in data['streams'])
    num, den = v['avg_frame_rate'].split('/')
    fps = float(num) / float(den)
    return {
        'duration': float(data['format']['duration']),
        'fps': fps,
        'has_audio': has_audio,
        'w': v['width'],
        'h': v['height'],
    }


TEMP_FILES = [
    'part1_body.mp4', 'p1_tail.mp4',
    'p2_head.mp4', 'part2_body.mp4', 'p2_tail.mp4',
    'p2_bridge.mp4', 'p2_bulk.mp4', 'p2_tail_bridge.mp4',
    'p3_head.mp4', 'part3_body.mp4',
    'trans_a.mp4', 'trans_b.mp4',
    'concat_list.txt', 'body2_concat.txt',
]


def cleanup():
    for f in TEMP_FILES:
        if os.path.exists(f):
            os.remove(f)


def run(cmd):
    print('Running:', ' '.join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True)


def main():
    missing = [f for f in ('1.mp4', '2.mp4', '3.mp4') if not os.path.exists(f)]
    if missing:
        raise FileNotFoundError(
            f"Missing input file(s): {', '.join(missing)}. "
            "Place all three video files in the same folder as this script."
        )

    info1 = get_info('1.mp4')
    info2 = get_info('2.mp4')
    info3 = get_info('3.mp4')

    # ── Target spec: everything must match video 2 ───────────────────────────
    fps   = info2['fps']        # 25
    w     = info2['w']          # e.g. 1920
    h     = info2['h']          # e.g. 1080
    fps_s = str(int(fps)) if fps == int(fps) else str(fps)

    # We force ALL segments to use mp4 container timescale 25000.
    # Video 2's native tbn is 30000 tbn; the re-encoded segments default to
    # 12800 tbn.  If these are mixed in a stream-copy concat the player
    # misinterprets PTS values and the video plays at 30000/12800 ≈ 2.34×
    # normal speed — turning an 80-minute video into a ~3-hour one.
    TBN = '25000'

    # Re-encoding settings for all transition segments.
    # -video_track_timescale 25000 forces the mp4 container tbn to 25000.
    # -crf 18 gives higher quality than default (23) so the 1-second
    # re-encoded clips don't look softer than the stream-copied body.
    v_enc = [
        '-c:v', 'libx264', '-profile:v', 'baseline', '-level', '4.0',
        '-pix_fmt', 'yuv420p', '-r', fps_s, '-crf', '18',
        '-video_track_timescale', TBN,
    ]
    a_enc = ['-c:a', 'aac', '-ar', '48000', '-ac', '2', '-b:a', '128k']

    # Filter to scale + convert fps for clips from videos 1 & 3 (30 fps → 25 fps)
    scale_fps = f'scale={w}:{h}:flags=lanczos,fps={fps_s}'

    print("=" * 60)
    print("Source info:")
    print(f"  1.mp4 : {info1['duration']:.3f}s  {info1['fps']}fps  audio={info1['has_audio']}")
    print(f"  2.mp4 : {info2['duration']:.3f}s  {info2['fps']}fps  audio={info2['has_audio']}")
    print(f"  3.mp4 : {info3['duration']:.3f}s  {info3['fps']}fps  audio={info3['has_audio']}")
    print(f"Target  : {fps}fps  {w}x{h}  tbn={TBN}")
    print("=" * 60)

    body1_dur = round(info1['duration'] - 1.0, 6)
    body2_dur = round(info2['duration'] - 2.0, 6)
    body3_dur = round(info3['duration'] - 1.0, 6)

    print("\n--- Phase 1: Preparing Segments ---")

    # ── part1_body: all of video 1 except the last 1 s ───────────────────────
    run(['ffmpeg', '-y',
         '-i', '1.mp4',
         '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
         '-t', str(body1_dur),
         '-filter_complex', f'[0:v]{scale_fps}[v]',
         '-map', '[v]', '-map', '1:a',
         ] + v_enc + a_enc + ['part1_body.mp4'])

    # ── p1_tail: last 1 s of video 1  (input to transition A) ───────────────
    run(['ffmpeg', '-y',
         '-ss', str(info1['duration'] - 1.0), '-i', '1.mp4',
         '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
         '-t', '1',
         '-filter_complex', f'[0:v]{scale_fps}[v]',
         '-map', '[v]', '-map', '1:a',
         ] + v_enc + a_enc + ['p1_tail.mp4'])

    # ── p2_head: first 1 s of video 2  (output of transition A) ─────────────
    # Video 2 has unusual stream order: audio=stream 0, video=stream 1.
    # Explicit -map avoids picking the wrong stream.
    run(['ffmpeg', '-y',
         '-i', '2.mp4',
         '-t', '1',
         '-map', '0:v', '-map', '0:a',
         ] + v_enc + a_enc + ['p2_head.mp4'])

    # ── part2_body: video 2 from t=1 s to (duration − 1 s) ──────────────────
    #
    # The fundamental problem with stream-copying around a non-keyframe boundary:
    #
    #   H.264 video can only be cut losslessly (stream-copy) at keyframe (IDR)
    #   boundaries.  t=1s almost certainly does NOT land on a keyframe in a
    #   1-hour video (GOP size is typically 2–5 s).  This means:
    #
    #   • Input-seek (-ss before -i): seeks to keyframe BEFORE t=1s (e.g. t=0),
    #     copies from there → first second of video 2 plays twice (the jerk).
    #
    #   • Output-seek (-ss after -i): scans forward and starts copying from the
    #     keyframe AFTER t=1s (e.g. t=2s), skipping t=1s..2s entirely → player
    #     holds the last frame of trans_a for ~1s (the freeze).
    #
    # Solution — keyframe-split approach:
    #   1. Find K = timestamp of first keyframe at or after t=1s in video 2.
    #   2. Re-encode the tiny "bridge" segment t=1s..K with exact frame accuracy.
    #      (This is only a fraction of a second, so it's fast.)
    #   3. Stream-copy the bulk from K to (last keyframe before duration-1s).
    #      At K we are guaranteed to be on a keyframe, so -c:v copy is clean.
    #   4. Concatenate bridge + bulk into part2_body.mp4.
    #
    # Similarly find K2 = last keyframe before (duration-1s) for the tail cut,
    # but for the tail we re-encode anyway (p2_tail), so we just need the bulk
    # to end exactly at (duration-1s) — which we handle by re-encoding p2_tail
    # from input-seek to the keyframe before duration-1s, and ending bulk copy
    # at that same keyframe.
    #
    # Step 1: find keyframe timestamps in the region we care about.
    print("  Finding keyframe boundaries in 2.mp4 ...")
    kf_cmd = [
        'ffprobe', '-v', 'quiet', '-print_format', 'json',
        '-select_streams', 'v:0',
        '-skip_frame', 'noref',           # only look at keyframes
        '-show_frames',
        '-read_intervals', f'%+{min(info2["duration"], 30)}',  # scan first 30 s max
        '-show_entries', 'frame=pts_time,pkt_pts_time,key_frame',
        '2.mp4'
    ]
    kf_data = json.loads(subprocess.check_output(kf_cmd).decode('utf-8'))
    def _pts(f):
        # ffprobe 7+ uses 'pts_time'; older builds used 'pkt_pts_time'
        return f.get('pts_time') or f.get('pkt_pts_time')
    keyframe_times = [
        float(_pts(f))
        for f in kf_data.get('frames', [])
        if f.get('key_frame') == 1 and _pts(f) is not None
    ]

    # First keyframe at or after t=1s — this is where bulk copy can safely start.
    kf_after_1 = next((t for t in keyframe_times if t >= 1.0), None)
    if kf_after_1 is None:
        # Fallback: no keyframe found after 1s in first 30s — unlikely but safe.
        # Re-encode the entire body (slow but correct).
        print("  WARNING: no keyframe found after t=1s; re-encoding entire body.")
        run(['ffmpeg', '-y',
             '-i', '2.mp4',
             '-ss', '1', '-t', str(body2_dur),
             '-map', '0:v', '-map', '0:a',
             ] + v_enc + a_enc + ['part2_body.mp4'])
    else:
        print(f"  First keyframe at or after t=1s: {kf_after_1:.4f}s")
        bridge_dur = round(kf_after_1 - 1.0, 6)
        # Also find last keyframe before (duration-1s) for the tail-end cut.
        # Scan the last 30 s of the file for the tail boundary.
        tail_start_approx = info2['duration'] - 1.0
        kf_cmd2 = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-select_streams', 'v:0',
            '-skip_frame', 'noref',
            '-show_frames',
            '-read_intervals', f'{max(0, tail_start_approx - 30)}%+30',
            '-show_entries', 'frame=pts_time,pkt_pts_time,key_frame',
            '2.mp4'
        ]
        kf_data2 = json.loads(subprocess.check_output(kf_cmd2).decode('utf-8'))
        keyframe_times2 = [
            float(_pts(f))
            for f in kf_data2.get('frames', [])
            if f.get('key_frame') == 1 and _pts(f) is not None
        ]
        # Last keyframe strictly before (duration-1s)
        kf_before_tail = max(
            (t for t in keyframe_times2 if t < tail_start_approx),
            default=kf_after_1
        )
        print(f"  Last keyframe before tail at t={tail_start_approx:.3f}s: {kf_before_tail:.4f}s")

        bulk_dur = round(kf_before_tail - kf_after_1, 6)

        if bridge_dur > 0.001:
            # Step 2: Re-encode the bridge (t=1s → kf_after_1).
            # Output-seek from t=1s: exact frame accuracy, only a tiny segment.
            run(['ffmpeg', '-y',
                 '-i', '2.mp4',
                 '-ss', '1', '-t', str(bridge_dur),
                 '-map', '0:v', '-map', '0:a',
                 ] + v_enc + a_enc + ['p2_bridge.mp4'])
        else:
            # kf_after_1 is essentially at t=1s — no bridge needed.
            bridge_dur = 0.0

        # Step 3: Stream-copy the bulk (kf_after_1 → kf_before_tail).
        # Input-seek to kf_after_1 is safe because that IS a keyframe.
        run(['ffmpeg', '-y',
             '-ss', str(kf_after_1), '-i', '2.mp4',
             '-t', str(bulk_dur),
             '-map', '0:v', '-map', '0:a',
             '-c:v', 'copy', '-c:a', 'copy',
             '-avoid_negative_ts', 'make_zero',
             '-video_track_timescale', TBN,
             'p2_bulk.mp4'])

        # Step 4: Re-encode the tail bridge (kf_before_tail → duration-1s).
        # This covers the non-keyframe-aligned end of the body.
        tail_bridge_dur = round(tail_start_approx - kf_before_tail, 6)
        if tail_bridge_dur > 0.001:
            run(['ffmpeg', '-y',
                 '-ss', str(kf_before_tail), '-i', '2.mp4',
                 '-t', str(tail_bridge_dur),
                 '-map', '0:v', '-map', '0:a',
                 ] + v_enc + a_enc + ['p2_tail_bridge.mp4'])

        # Step 5: Assemble part2_body from its pieces.
        body_pieces = []
        if bridge_dur > 0.001:
            body_pieces.append("file 'p2_bridge.mp4'")
        body_pieces.append("file 'p2_bulk.mp4'")
        if tail_bridge_dur > 0.001:
            body_pieces.append("file 'p2_tail_bridge.mp4'")

        with open('body2_concat.txt', 'w') as f:
            f.write('\n'.join(body_pieces) + '\n')

        run(['ffmpeg', '-y',
             '-f', 'concat', '-safe', '0', '-i', 'body2_concat.txt',
             '-c', 'copy',
             'part2_body.mp4'])

        # Clean up body assembly temp files
        for tf in ['p2_bridge.mp4', 'p2_bulk.mp4', 'p2_tail_bridge.mp4', 'body2_concat.txt']:
            if os.path.exists(tf):
                os.remove(tf)

    # ── p2_tail: last 1 s of video 2  (input to transition B) ───────────────
    run(['ffmpeg', '-y',
         '-ss', str(info2['duration'] - 1.0), '-i', '2.mp4',
         '-t', '1',
         '-map', '0:v', '-map', '0:a',
         ] + v_enc + a_enc + ['p2_tail.mp4'])

    # ── p3_head: first 1 s of video 3  (output of transition B) ─────────────
    run(['ffmpeg', '-y',
         '-i', '3.mp4',
         '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
         '-t', '1',
         '-filter_complex', f'[0:v]{scale_fps}[v]',
         '-map', '[v]', '-map', '1:a',
         ] + v_enc + a_enc + ['p3_head.mp4'])

    # ── part3_body: video 3 from t=1 s to end ────────────────────────────────
    # -t MUST be set: anullsrc is an infinite source; without -t ffmpeg encodes
    # forever (the original infinite-loop bug in the Gemini script).
    run(['ffmpeg', '-y',
         '-ss', '1', '-i', '3.mp4',
         '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
         '-t', str(body3_dur),
         '-filter_complex', f'[0:v]{scale_fps}[v]',
         '-map', '[v]', '-map', '1:a',
         ] + v_enc + a_enc + ['part3_body.mp4'])

    print("\n--- Phase 2: Rendering Transitions ---")

    # xfade  — crossfades the two video streams over 1 s
    # acrossfade — crossfades the two audio streams over 1 s
    # (afade only fades a single stream and cannot mix two streams together)
    fc = ('[0:v][1:v]xfade=transition=fade:duration=1:offset=0[v];'
          '[0:a][1:a]acrossfade=d=1[a]')

    # Transition A: end of video 1 → start of video 2
    run(['ffmpeg', '-y',
         '-i', 'p1_tail.mp4', '-i', 'p2_head.mp4',
         '-filter_complex', fc,
         '-map', '[v]', '-map', '[a]',
         ] + v_enc + a_enc + ['trans_a.mp4'])

    # Transition B: end of video 2 → start of video 3
    run(['ffmpeg', '-y',
         '-i', 'p2_tail.mp4', '-i', 'p3_head.mp4',
         '-filter_complex', fc,
         '-map', '[v]', '-map', '[a]',
         ] + v_enc + a_enc + ['trans_b.mp4'])

    print("\n--- Phase 3: Verifying segment timebases ---")
    segments = ['part1_body.mp4', 'trans_a.mp4', 'part2_body.mp4',
                'trans_b.mp4', 'part3_body.mp4']
    all_ok = True
    for seg in segments:
        result = subprocess.check_output(
            ['ffprobe', '-v', 'quiet', '-print_format', 'json',
             '-show_streams', seg]).decode('utf-8')
        streams = json.loads(result)['streams']
        for s in streams:
            tbn = s.get('time_base', '?')
            print(f"  {seg}  [{s['codec_type']}]  tbn={tbn}  codec={s.get('codec_name','?')}")
            # time_base is expressed as "1/N" — check N == TBN
            if s['codec_type'] == 'video' and tbn != f'1/{TBN}':
                print(f"  WARNING: expected tbn=1/{TBN}, got {tbn}")
                all_ok = False
    if all_ok:
        print("  All video timebases match — concat will be clean.")
    else:
        print("  Timebase mismatch detected. The final video may have sync issues.")

    print("\n--- Phase 4: Final Concatenation ---")
    with open('concat_list.txt', 'w') as f:
        f.write(
            "file 'part1_body.mp4'\n"
            "file 'trans_a.mp4'\n"
            "file 'part2_body.mp4'\n"
            "file 'trans_b.mp4'\n"
            "file 'part3_body.mp4'\n"
        )

    # All segments share tbn=25000, same codec, same fps, same resolution →
    # stream-copy concat is safe and lossless for the body of video 2.
    run(['ffmpeg', '-y',
         '-f', 'concat', '-safe', '0', '-i', 'concat_list.txt',
         '-c', 'copy',
         'final_output.mp4'])

    print("\n--- Cleanup ---")
    cleanup()

    print("\nDone!  Output: final_output.mp4")
    result = subprocess.check_output(
        ['ffprobe', '-v', 'quiet', '-print_format', 'json',
         '-show_format', 'final_output.mp4']).decode('utf-8')
    dur = float(json.loads(result)['format']['duration'])
    print(f"Final duration: {dur:.1f}s  ({dur/60:.1f} min)")


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt):
        print("\nAn error occurred. Cleaning up temporary files ...")
        cleanup()
        raise