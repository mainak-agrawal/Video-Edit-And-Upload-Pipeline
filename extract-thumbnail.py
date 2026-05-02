import subprocess
import json


def get_info(path):
    cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json',
           '-show_streams', '-show_format', path]
    data = json.loads(subprocess.check_output(cmd).decode('utf-8'))
    v = next(s for s in data['streams'] if s['codec_type'] == 'video')
    num, den = v['avg_frame_rate'].split('/')
    fps = float(num) / float(den)
    return {
        'duration': float(data['format']['duration']),
        'fps': fps,
        'w': v['width'],
        'h': v['height'],
    }


def main():
    info = get_info('1.mp4')
    duration = info['duration']
    fps = info['fps']

    # We want a frame that is fully loaded (animation complete) but not the
    # very last frame, which can sometimes be mid-transition or slightly faded.
    # Strategy: extract from 85% through the clip. For a 5s clip that's t=4.25s,
    # which is comfortably in the final "settled" state of the animation while
    # avoiding the last few frames.
    target_t = duration * 0.85

    # Round to the nearest frame boundary so ffmpeg lands exactly on a real frame.
    frame_index = round(target_t * fps)
    target_t = frame_index / fps

    print(f"Video: {duration:.3f}s  {fps}fps  {info['w']}x{info['h']}")
    print(f"Extracting frame {frame_index} at t={target_t:.4f}s  "
          f"({target_t/duration*100:.1f}% through the clip)")

    # -ss before -i: fast input seek to near the target
    # -vf select: pick the exact frame by number for pixel-perfect accuracy
    # -vframes 1: output exactly one frame
    # -q:v 1: highest JPEG quality (1=best, 31=worst); ignored for PNG but harmless
    # PNG is lossless so thumbnail.png will be full quality
    subprocess.run([
        'ffmpeg', '-y',
        '-ss', str(target_t),
        '-i', '1.mp4',
        '-vframes', '1',
        '-q:v', '1',
        'thumbnail.png'
    ], check=True)

    print("Saved: thumbnail.png")


if __name__ == '__main__':
    main()