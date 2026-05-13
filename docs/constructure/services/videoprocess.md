# VideoProcess Service

## Overview

Video processing services including Faster Whisper for transcription.

## Tech Stack

- **Language**: Python
- **Tools**: Faster Whisper, audio processing

## Services

| Service | Description |
|---------|-------------|
| **FasterWhisper** | Fast Whisper transcription |
| **TextToAudio** | Text to audio conversion |

## Project Structure

```
VideoProcess/
├── FasterWhisper/            # Whisper transcription
│   ├── fw_srt.py            # SRT generation
│   └── fw_srt_stream.py    # Streaming transcription
├── TextToAudio/             # Text to audio
└── voice_chat_bot/          # Voice chat bot
```
