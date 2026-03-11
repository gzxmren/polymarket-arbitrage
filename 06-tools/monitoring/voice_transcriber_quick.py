#!/usr/bin/env python3
"""
语音转文字 - 快速版（使用小模型）
"""

import os
import sys
import subprocess
from pathlib import Path

# 禁用代理
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''
os.environ['ALL_PROXY'] = ''
os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['all_proxy'] = ''

from faster_whisper import WhisperModel

# 使用小模型，下载更快
MODEL_SIZE = "base"  # tiny, base, small, medium, large-v1/2/3

_model = None


def get_model():
    """获取或加载模型"""
    global _model
    if _model is None:
        print(f"🔄 加载 Whisper 模型: {MODEL_SIZE} ...")
        _model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
        print(f"✅ 模型加载完成")
    return _model


def convert_to_wav(input_path: str) -> str:
    """将音频转换为 WAV 格式"""
    output_path = "/tmp/whisper_input.wav"
    
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-ar", "16000",
        "-ac", "1",
        "-c:a", "pcm_s16le",
        output_path
    ]
    
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return output_path
    except subprocess.CalledProcessError as e:
        print(f"❌ 音频转换失败: {e}")
        return None


def transcribe(audio_path: str) -> dict:
    """转录音频"""
    if not os.path.exists(audio_path):
        return {"error": f"文件不存在: {audio_path}"}
    
    # 转换格式
    wav_path = convert_to_wav(audio_path)
    if not wav_path:
        return {"error": "音频转换失败"}
    
    # 加载模型并转录
    model = get_model()
    
    print(f"🎯 开始转录...")
    segments, info = model.transcribe(
        wav_path,
        language="zh",  # 指定中文
        task="transcribe",
        beam_size=5,
        vad_filter=True
    )
    
    # 收集结果
    text_parts = []
    for segment in segments:
        text_parts.append(segment.text)
    
    result = {
        "text": "".join(text_parts).strip(),
        "language": info.language,
        "duration": info.duration
    }
    
    # 清理
    if os.path.exists(wav_path):
        os.remove(wav_path)
    
    return result


def main():
    if len(sys.argv) < 2:
        print("Usage: python voice_transcriber_quick.py <audio_file>")
        sys.exit(1)
    
    audio_file = sys.argv[1]
    result = transcribe(audio_file)
    
    if "error" in result:
        print(f"❌ {result['error']}")
        sys.exit(1)
    
    print(f"\n🎯 转录结果")
    print(f"语言: {result['language']}")
    print(f"时长: {result['duration']:.1f} 秒")
    print(f"\n📝 内容:\n{result['text']}")


if __name__ == "__main__":
    main()
