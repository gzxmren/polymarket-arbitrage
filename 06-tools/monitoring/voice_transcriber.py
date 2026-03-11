#!/usr/bin/env python3
"""
语音转文字 - 使用 faster-whisper
本地运行，完全免费，准确率高
"""

import os
import sys
import subprocess
from pathlib import Path
from faster_whisper import WhisperModel

# 模型缓存目录
MODEL_DIR = Path.home() / ".cache" / "whisper"
MODEL_SIZE = "large-v3"  # 可选: tiny, base, small, medium, large-v1, large-v2, large-v3

# 全局模型实例（延迟加载）
_model = None


def get_model():
    """获取或加载模型（单例模式）"""
    global _model
    if _model is None:
        print(f"🔄 加载 Whisper 模型: {MODEL_SIZE} ...")
        # 使用 CPU，4线程
        _model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8", cpu_threads=4)
        print(f"✅ 模型加载完成")
    return _model


def convert_to_wav(input_path: str) -> str:
    """将音频转换为 WAV 格式"""
    output_path = "/tmp/whisper_input.wav"
    
    # 使用 ffmpeg 转换
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-ar", "16000",  # 16kHz 采样率
        "-ac", "1",       # 单声道
        "-c:a", "pcm_s16le",  # 16位 PCM
        output_path
    ]
    
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return output_path
    except subprocess.CalledProcessError as e:
        print(f"❌ 音频转换失败: {e}")
        return None


def transcribe(audio_path: str, language: str = None) -> dict:
    """
    转录音频文件
    
    Args:
        audio_path: 音频文件路径
        language: 语言代码 (如 'zh', 'en')，None 则自动检测
    
    Returns:
        dict: {text: 转录文本, language: 检测到的语言, segments: 分段详情}
    """
    # 检查文件
    if not os.path.exists(audio_path):
        return {"error": f"文件不存在: {audio_path}"}
    
    # 转换为 WAV
    wav_path = convert_to_wav(audio_path)
    if not wav_path:
        return {"error": "音频转换失败"}
    
    # 加载模型
    model = get_model()
    
    # 转录
    print(f"🎯 开始转录: {audio_path}")
    segments, info = model.transcribe(
        wav_path,
        language=language,
        task="transcribe",
        beam_size=5,
        best_of=5,
        temperature=0.0,
        condition_on_previous_text=True,
        vad_filter=True,  # 启用语音活动检测
        vad_parameters=dict(min_silence_duration_ms=500)
    )
    
    # 收集结果
    text_parts = []
    segment_details = []
    
    for segment in segments:
        text_parts.append(segment.text)
        segment_details.append({
            "start": segment.start,
            "end": segment.end,
            "text": segment.text,
            "confidence": segment.avg_logprob
        })
    
    result = {
        "text": "".join(text_parts).strip(),
        "language": info.language,
        "language_probability": info.language_probability,
        "duration": info.duration,
        "segments": segment_details
    }
    
    # 清理临时文件
    if os.path.exists(wav_path):
        os.remove(wav_path)
    
    return result


def transcribe_telegram_voice(ogg_path: str) -> str:
    """
    转录 Telegram 语音消息（专用接口）
    
    Args:
        ogg_path: Telegram 语音文件路径 (.ogg)
    
    Returns:
        str: 转录后的文字
    """
    result = transcribe(ogg_path)
    
    if "error" in result:
        return f"❌ 转录失败: {result['error']}"
    
    # 格式化输出
    output = [
        f"🎯 语音转文字完成",
        f"",
        f"📝 内容:",
        f"{result['text']}",
        f"",
        f"📊 信息:",
        f"   语言: {result['language']} (置信度: {result['language_probability']:.1%})",
        f"   时长: {result['duration']:.1f} 秒",
        f"   分段: {len(result['segments'])} 段",
    ]
    
    return "\n".join(output)


def main():
    """命令行入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description="语音转文字 (faster-whisper)")
    parser.add_argument("audio_file", help="音频文件路径")
    parser.add_argument("-l", "--language", help="语言代码 (如 zh, en)", default=None)
    parser.add_argument("-j", "--json", action="store_true", help="输出 JSON 格式")
    
    args = parser.parse_args()
    
    # 转录
    result = transcribe(args.audio_file, language=args.language)
    
    if "error" in result:
        print(f"❌ {result['error']}")
        sys.exit(1)
    
    if args.json:
        import json
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"\n🎯 转录结果")
        print(f"语言: {result['language']} (置信度: {result['language_probability']:.1%})")
        print(f"时长: {result['duration']:.1f} 秒")
        print(f"\n📝 内容:\n{result['text']}")


if __name__ == "__main__":
    main()
