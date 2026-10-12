import os
import sys
import uuid
import time
import shutil
import asyncio
from http import HTTPStatus
from urllib.request import urlopen

# ============================================================
# Windows 兼容补丁
# 减少 Windows + Python asyncio 退出时出现：
# Cancelling an overlapped future failed / WinError 6
# ============================================================

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import dashscope
import requests
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_from_directory, Response


# ============================================================
# 基础配置
# ============================================================

app = Flask(__name__)

# API Key 在 APP_DIR 确定后从环境变量或本地 .env 加载。

# 北京地域地址
dashscope.base_http_api_url = "https://dashscope.aliyuncs.com/api/v1"

# TTS 模型与音色
TTS_MODEL = "qwen3-tts-flash"
TTS_VOICE = "Maia"

# UE5 和口型同步更稳定的采样率
TARGET_SAMPLE_RATE = 48000

# ASR 推荐采样率
ASR_SAMPLE_RATE = 16000

# requests 超时时间，格式为：连接超时、读取超时
REQUEST_TIMEOUT = (10, 120)

# 程序所在目录。源码运行时是 server2.py 所在目录，打包后是 server2.exe 所在目录。
APP_DIR = (
    os.path.dirname(sys.executable)
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__))
)

# 本地配置不打包、不提交；系统环境变量优先。
load_dotenv(os.path.join(APP_DIR, ".env"))
DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "").strip()


# TTS 音频保存目录
TTS_OUTPUT_DIR = os.path.join(APP_DIR, "tts_output")
os.makedirs(TTS_OUTPUT_DIR, exist_ok=True)


# ============================================================
# 女性音色表
# ============================================================

FEMALE_VOICES = [
    {
        "id": "Cherry",
        "name": "芊悦",
        "description": "阳光积极、亲切自然小姐姐",
    },
    {
        "id": "Serena",
        "name": "苏瑶",
        "description": "温柔小姐姐",
    },
    {
        "id": "Chelsie",
        "name": "千雪",
        "description": "二次元虚拟女友",
    },
    {
        "id": "Momo",
        "name": "茉兔",
        "description": "撒娇搞怪，逗你开心",
    },
    {
        "id": "Vivian",
        "name": "十三",
        "description": "拽拽的、可爱的小暴躁",
    },
    {
        "id": "Maia",
        "name": "四月",
        "description": "知性与温柔的碰撞",
    },
    {
        "id": "Bella",
        "name": "萌宝",
        "description": "喝酒不打醉拳的小萝莉",
    },
    {
        "id": "Jennifer",
        "name": "詹妮弗",
        "description": "品牌级、电影质感般美语女声",
    },
    {
        "id": "Katerina",
        "name": "卡捷琳娜",
        "description": "御姐音色，韵律回味十足",
    },
    {
        "id": "Mia",
        "name": "乖小妹",
        "description": "温顺如春水，乖巧如初雪",
    },
    {
        "id": "Bellona",
        "name": "燕铮莺",
        "description": "声音洪亮，吐字清晰，人物鲜活",
    },
    {
        "id": "Bunny",
        "name": "萌小姬",
        "description": "萌属性爆棚的小萝莉",
    },
    {
        "id": "Elias",
        "name": "墨讲师",
        "description": "严谨又有叙事感的知识讲解音色",
    },
    {
        "id": "Nini",
        "name": "邻家妹妹",
        "description": "又软又黏的甜美嗓音",
    },
    {
        "id": "Seren",
        "name": "小婉",
        "description": "温和舒缓，适合助眠",
    },
    {
        "id": "Stella",
        "name": "少女阿月",
        "description": "甜美少女音，情绪表现鲜明",
    },
    {
        "id": "Sonrisa",
        "name": "索尼莎",
        "description": "热情开朗的拉美大姐",
    },
    {
        "id": "Sohee",
        "name": "素熙",
        "description": "温柔开朗，情绪丰富的韩国欧尼",
    },
    {
        "id": "Ono Anna",
        "name": "小野杏",
        "description": "鬼灵精怪的青梅竹马",
    },
    {
        "id": "Jada",
        "name": "上海-阿珍",
        "description": "风风火火的沪上阿姐",
    },
    {
        "id": "Sunny",
        "name": "四川-晴儿",
        "description": "甜到你心里的川妹子",
    },
    {
        "id": "Kiki",
        "name": "粤语-阿清",
        "description": "甜美的港妹闺蜜",
    },
]

VOICE_IDS = {voice["id"] for voice in FEMALE_VOICES}


# ============================================================
# 工具函数：日志与耗时
# ============================================================

def now_ms():
    return time.perf_counter()


def cost_seconds(start_time):
    return round(time.perf_counter() - start_time, 3)


def print_timing(label, start_time):
    print(f"{label}耗时：{cost_seconds(start_time)} 秒", flush=True)


# ============================================================
# 工具函数：从 DashScope 返回结果中提取音频 URL
# ============================================================

def extract_audio_url(response):
    """
    兼容不同形式的 DashScope 返回结构：
    response.output.audio.url
    response.output.audio["url"]
    response.output["audio"]["url"]
    """

    if response is None:
        return None

    output = getattr(response, "output", None)
    if output is None:
        return None

    audio = None

    if isinstance(output, dict):
        audio = output.get("audio")
    else:
        audio = getattr(output, "audio", None)

        if audio is None and hasattr(output, "get"):
            audio = output.get("audio")

    if audio is None:
        return None

    if isinstance(audio, dict):
        return audio.get("url")

    audio_url = getattr(audio, "url", None)
    if audio_url:
        return audio_url

    if hasattr(audio, "get"):
        return audio.get("url")

    return None


# ============================================================
# 工具函数：提取 ASR 识别文本
# ============================================================

def extract_asr_text(asr_response):
    output = getattr(asr_response, "output", None)
    recognized_text = ""

    if isinstance(output, dict):
        sentences = output.get("sentence") or []

        if sentences:
            recognized_text = "".join(
                sentence.get("text", "")
                for sentence in sentences
                if isinstance(sentence, dict)
            )
        else:
            recognized_text = output.get("text", "") or ""

    else:
        sentences = getattr(output, "sentence", None) if output is not None else None

        if sentences:
            text_parts = []
            for sentence in sentences:
                sentence_text = getattr(sentence, "text", "")
                text_parts.append(sentence_text)
            recognized_text = "".join(text_parts)
        elif output is not None:
            recognized_text = getattr(output, "text", "") or ""

    return recognized_text.strip()


# ============================================================
# 工具函数：下载 TTS 音频
# ============================================================

def download_audio(audio_url, save_path):
    """
    从 DashScope 返回的音频 URL 下载音频文件
    """

    with requests.get(audio_url, timeout=REQUEST_TIMEOUT, stream=True) as response:
        response.raise_for_status()

        with open(save_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 128):
                if chunk:
                    f.write(chunk)


# ============================================================
# 工具函数：判断音频是否已经符合目标格式
# ============================================================

def is_audio_already_ready(input_path, target_sample_rate, target_channels=1):
    """
    判断音频是否已经是：
    1. WAV
    2. PCM_16
    3. 目标采样率
    4. 目标声道数
    """

    try:
        info = sf.info(input_path)

        return (
            info.format == "WAV"
            and info.subtype == "PCM_16"
            and info.samplerate == target_sample_rate
            and info.channels == target_channels
        )

    except Exception:
        return False


# ============================================================
# 工具函数：高质量重采样
# ============================================================

def resample_audio(audio_data, source_sample_rate, target_sample_rate):
    """
    使用 resample_poly，比 scipy.signal.resample 更适合常规音频重采样。
    """

    if source_sample_rate == target_sample_rate:
        return audio_data

    from math import gcd

    common = gcd(source_sample_rate, target_sample_rate)
    up = target_sample_rate // common
    down = source_sample_rate // common

    return resample_poly(audio_data, up, down)


# ============================================================
# 工具函数：统一处理为目标 WAV
# ============================================================

def convert_audio_to_wav(
    input_path,
    output_path,
    target_sample_rate,
    target_channels=1,
):
    """
    统一处理音频为：
    1. 单声道
    2. 指定采样率
    3. PCM_16 WAV

    如果输入音频已经满足要求，直接复制，避免重复重采样。
    """

    if is_audio_already_ready(
        input_path=input_path,
        target_sample_rate=target_sample_rate,
        target_channels=target_channels,
    ):
        shutil.copyfile(input_path, output_path)
        return {
            "converted": False,
            "reason": "input_already_ready",
            "sample_rate": target_sample_rate,
            "channels": target_channels,
        }

    audio_data, sample_rate = sf.read(input_path)

    if audio_data is None or len(audio_data) == 0:
        raise ValueError(f"音频为空，无法处理：{input_path}")

    # 转 float32
    audio_data = np.asarray(audio_data, dtype=np.float32)

    # 转单声道
    if len(audio_data.shape) > 1:
        audio_data = np.mean(audio_data, axis=1)

    # 重采样
    if sample_rate != target_sample_rate:
        audio_data = resample_audio(
            audio_data=audio_data,
            source_sample_rate=sample_rate,
            target_sample_rate=target_sample_rate,
        )
        sample_rate = target_sample_rate

    # 防止数据溢出
    max_value = np.max(np.abs(audio_data))

    if max_value > 1.0:
        audio_data = audio_data / max_value

    # 保存为 PCM_16 WAV
    sf.write(output_path, audio_data, sample_rate, subtype="PCM_16")

    return {
        "converted": True,
        "reason": "converted_to_target_format",
        "sample_rate": sample_rate,
        "channels": target_channels,
    }


def convert_audio_for_ue5(input_path, output_path, target_sample_rate=48000):
    """
    统一处理为 UE5 / Lip Sync 更稳定的 48000Hz 单声道 PCM_16 WAV。
    """

    return convert_audio_to_wav(
        input_path=input_path,
        output_path=output_path,
        target_sample_rate=target_sample_rate,
        target_channels=1,
    )


def convert_audio_for_asr(input_path, output_path, target_sample_rate=16000):
    """
    统一处理为 ASR 更适合的 16000Hz 单声道 PCM_16 WAV。
    """

    return convert_audio_to_wav(
        input_path=input_path,
        output_path=output_path,
        target_sample_rate=target_sample_rate,
        target_channels=1,
    )


# ============================================================
# 接口：健康检查
# ============================================================

@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({
        "status": "success",
        "message": "Flask TTS/ASR service is running",
        "tts_output_dir": TTS_OUTPUT_DIR,
        "current_voice": TTS_VOICE,
    })


@app.route("/favicon.ico", methods=["GET"])
def favicon():
    return Response(status=204)


# ============================================================
# 接口：语音识别
# ============================================================

@app.route("/recognize_speech", methods=["POST"])
def recognize_speech():
    """
    UE5 调用方式：
    POST http://127.0.0.1:5000/recognize_speech

    JSON 示例：
    {
        "audio_path": "D:/UE_Project/Saved/TempAudio/recording.wav"
    }

    返回示例：
    {
        "status": "success",
        "text": "你好，我想和你聊聊天。",
        "timing": {
            "convert": 0.12,
            "asr": 0.85,
            "total": 1.02
        }
    }
    """

    total_start = now_ms()
    temp_audio_path = None

    try:
        data = request.get_json(force=True)

        audio_path = (
            data.get("audio_path")
            or data.get("file_path")
            or data.get("path")
            or ""
        ).strip()

        if not audio_path:
            return jsonify({
                "status": "error",
                "message": "缺少 audio_path 字段，无法识别语音"
            }), 400

        audio_path = os.path.abspath(audio_path)

        if not os.path.exists(audio_path):
            return jsonify({
                "status": "error",
                "message": f"音频文件不存在：{audio_path}"
            }), 404

        if not DASHSCOPE_API_KEY:
            return jsonify({
                "status": "error",
                "message": "请在环境变量或本地 .env 中配置 DASHSCOPE_API_KEY"
            }), 503

        print("\n========== ASR 请求开始 ==========", flush=True)
        print("收到 ASR 音频：", audio_path, flush=True)

        file_id = uuid.uuid4().hex
        temp_audio_path = os.path.join(
            TTS_OUTPUT_DIR,
            f"asr_{file_id}_16k.wav"
        )

        # 1. 音频转换
        convert_start = now_ms()

        convert_info = convert_audio_for_asr(
            input_path=audio_path,
            output_path=temp_audio_path,
            target_sample_rate=ASR_SAMPLE_RATE
        )

        convert_cost = cost_seconds(convert_start)
        print("ASR 音频处理信息：", convert_info, flush=True)
        print("ASR 音频处理耗时：", convert_cost, "秒", flush=True)

        # 2. 调用 ASR
        asr_start = now_ms()

        recognizer = dashscope.audio.asr.Recognition(
            model="paraformer-realtime-v1",
            format="wav",
            sample_rate=ASR_SAMPLE_RATE,
            callback=None,
        )

        asr_response = recognizer.call(
            file=temp_audio_path,
            api_key=DASHSCOPE_API_KEY
        )

        asr_cost = cost_seconds(asr_start)

        print("DashScope ASR 返回：", asr_response, flush=True)
        print("DashScope ASR 调用耗时：", asr_cost, "秒", flush=True)

        status_code = getattr(asr_response, "status_code", None)

        if status_code != HTTPStatus.OK and status_code != 200:
            error_code = getattr(asr_response, "code", "unknown_error")
            error_message = getattr(asr_response, "message", "ASR 调用失败")

            return jsonify({
                "status": "error",
                "message": "语音识别调用失败",
                "code": str(error_code),
                "detail": str(error_message),
                "timing": {
                    "convert": convert_cost,
                    "asr": asr_cost,
                    "total": cost_seconds(total_start),
                }
            }), 500

        recognized_text = extract_asr_text(asr_response)

        print("ASR 识别结果：", recognized_text, flush=True)
        print("ASR 总耗时：", cost_seconds(total_start), "秒", flush=True)
        print("========== ASR 请求结束 ==========\n", flush=True)

        return jsonify({
            "status": "success",
            "text": recognized_text,
            "timing": {
                "convert": convert_cost,
                "asr": asr_cost,
                "total": cost_seconds(total_start),
            }
        })

    except Exception as e:
        print("recognize_speech 接口异常：", str(e), flush=True)

        return jsonify({
            "status": "error",
            "message": str(e),
            "timing": {
                "total": cost_seconds(total_start),
            }
        }), 500

    finally:
        if temp_audio_path and os.path.exists(temp_audio_path):
            try:
                os.remove(temp_audio_path)
            except OSError:
                pass


# ============================================================
# 接口：生成语音
# ============================================================

@app.route("/generate_voice", methods=["POST"])
def generate_voice():
    """
    UE5 调用方式：
    POST http://127.0.0.1:5000/generate_voice

    JSON 示例：
    {
        "text": "你好，我是林星璃。",
        "voice": "Maia"
    }

    返回示例：
    {
        "status": "success",
        "audio_path": "D:/xxx/tts_output/tts_xxx_ue5.wav",
        "audio_url": "/audio/tts_xxx_ue5.wav",
        "text_echo": "你好，我是林星璃。",
        "voice": "Maia",
        "timing": {
            "tts": 1.5,
            "download": 0.3,
            "convert": 0.1,
            "total": 1.9
        }
    }
    """

    total_start = now_ms()
    raw_audio_path = None

    try:
        data = request.get_json(force=True)

        text = (
            data.get("text")
            or data.get("reply_text")
            or data.get("content")
            or ""
        ).strip()

        if not text:
            return jsonify({
                "status": "error",
                "message": "缺少 text 字段，无法生成语音"
            }), 400

        voice = (data.get("voice") or TTS_VOICE).strip()

        if voice not in VOICE_IDS:
            return jsonify({
                "status": "error",
                "message": f"不支持的音色：{voice}",
                "supported_voices": sorted(VOICE_IDS)
            }), 400

        if not DASHSCOPE_API_KEY:
            return jsonify({
                "status": "error",
                "message": "请在环境变量或本地 .env 中配置 DASHSCOPE_API_KEY"
            }), 503

        print("\n========== TTS 请求开始 ==========", flush=True)
        print("收到 TTS 文本：", text, flush=True)
        print("TTS 文本长度：", len(text), "字符", flush=True)
        print("使用 TTS 音色：", voice, flush=True)

        # ====================================================
        # 1. 调用 Qwen3-TTS
        # ====================================================

        tts_start = now_ms()

        tts_response = dashscope.MultiModalConversation.call(
            model=TTS_MODEL,
            api_key=DASHSCOPE_API_KEY,
            text=text,
            voice=voice
        )

        tts_cost = cost_seconds(tts_start)

        print("DashScope TTS 返回：", tts_response, flush=True)
        print("DashScope TTS 调用耗时：", tts_cost, "秒", flush=True)

        status_code = getattr(tts_response, "status_code", None)

        if status_code != HTTPStatus.OK and status_code != 200:
            error_code = getattr(tts_response, "code", "unknown_error")
            error_message = getattr(tts_response, "message", "TTS 调用失败")

            return jsonify({
                "status": "error",
                "message": "Qwen3-TTS 调用失败",
                "code": str(error_code),
                "detail": str(error_message),
                "timing": {
                    "tts": tts_cost,
                    "total": cost_seconds(total_start),
                }
            }), 500

        audio_url = extract_audio_url(tts_response)

        if not audio_url:
            return jsonify({
                "status": "error",
                "message": "TTS 成功返回，但没有找到音频 URL",
                "raw_response": str(tts_response),
                "timing": {
                    "tts": tts_cost,
                    "total": cost_seconds(total_start),
                }
            }), 500

        print("TTS 音频 URL：", audio_url, flush=True)

        # ====================================================
        # 2. 下载音频
        # ====================================================

        file_id = uuid.uuid4().hex

        raw_audio_path = os.path.join(
            TTS_OUTPUT_DIR,
            f"tts_{file_id}_raw.wav"
        )

        final_audio_path = os.path.join(
            TTS_OUTPUT_DIR,
            f"tts_{file_id}_ue5.wav"
        )

        download_start = now_ms()

        download_audio(audio_url, raw_audio_path)

        download_cost = cost_seconds(download_start)
        print("TTS 音频下载耗时：", download_cost, "秒", flush=True)

        # ====================================================
        # 3. 转为 UE5 / LipSync 更稳定的 48000Hz 单声道 WAV
        # ====================================================

        convert_start = now_ms()

        convert_info = convert_audio_for_ue5(
            input_path=raw_audio_path,
            output_path=final_audio_path,
            target_sample_rate=TARGET_SAMPLE_RATE
        )

        convert_cost = cost_seconds(convert_start)

        print("TTS 音频处理信息：", convert_info, flush=True)
        print("TTS 音频处理耗时：", convert_cost, "秒", flush=True)
        print("最终返回 UE5 的音频路径：", final_audio_path, flush=True)
        print("TTS 总耗时：", cost_seconds(total_start), "秒", flush=True)
        print("========== TTS 请求结束 ==========\n", flush=True)

        return jsonify({
            "status": "success",
            "audio_path": final_audio_path,
            "audio_url": f"/audio/{os.path.basename(final_audio_path)}",
            "text_echo": text,
            "voice": voice,
            "timing": {
                "tts": tts_cost,
                "download": download_cost,
                "convert": convert_cost,
                "total": cost_seconds(total_start),
            }
        })

    except Exception as e:
        print("generate_voice 接口异常：", str(e), flush=True)

        return jsonify({
            "status": "error",
            "message": str(e),
            "timing": {
                "total": cost_seconds(total_start),
            }
        }), 500

    finally:
        # raw 临时音频用完就删，只保留最终给 UE5 使用的 _ue5.wav
        if raw_audio_path and os.path.exists(raw_audio_path):
            try:
                os.remove(raw_audio_path)
            except OSError:
                pass


# ============================================================
# 接口：获取和切换音色
# ============================================================

@app.route("/voices", methods=["GET"])
def get_voices():
    return jsonify({
        "status": "success",
        "current_voice": TTS_VOICE,
        "voices": FEMALE_VOICES,
    })


@app.route("/set_voice", methods=["POST"])
def set_voice():
    global TTS_VOICE

    try:
        data = request.get_json(force=True)
    except Exception:
        data = {}

    voice = (data.get("voice") or "").strip()

    if voice not in VOICE_IDS:
        return jsonify({
            "status": "error",
            "message": f"不支持的音色：{voice}",
            "supported_voices": sorted(VOICE_IDS)
        }), 400

    TTS_VOICE = voice
    print("默认 TTS 音色已切换为：", TTS_VOICE, flush=True)

    return jsonify({
        "status": "success",
        "current_voice": TTS_VOICE
    })


@app.route("/routes", methods=["GET"])
def get_routes():
    return jsonify({
        "status": "success",
        "routes": sorted(str(rule) for rule in app.url_map.iter_rules())
    })


@app.route("/audio/<path:file_name>", methods=["GET"])
def get_audio(file_name):
    return send_from_directory(TTS_OUTPUT_DIR, file_name)


# ============================================================
# 页面：本地音色控制台
# ============================================================

@app.route("/", methods=["GET"])
def index():
    voice_options = "\n".join(
        (
            f'<option value="{voice["id"]}">'
            f'{voice["id"]} / {voice["name"]} - {voice["description"]}'
            f'</option>'
        )
        for voice in FEMALE_VOICES
    )

    html = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>数字人音色控制台</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 28px 16px;
      background: #f4f7fb;
      color: #172033;
      font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
    }
    main {
      width: min(760px, 100%);
      padding: 28px;
      border: 1px solid #dfe7f3;
      border-radius: 8px;
      background: #fff;
      box-shadow: 0 18px 48px rgba(22, 34, 51, .10);
    }
    h1 {
      margin: 0 0 8px;
      font-size: 26px;
      letter-spacing: 0;
    }
    p {
      margin: 0 0 22px;
      color: #5b6678;
      line-height: 1.6;
    }
    label {
      display: block;
      margin: 18px 0 8px;
      font-weight: 700;
    }
    select, textarea {
      width: 100%;
      border: 1px solid #c9d5e6;
      border-radius: 6px;
      background: #fff;
      color: #172033;
      font: inherit;
    }
    select {
      min-height: 44px;
      padding: 0 12px;
    }
    textarea {
      min-height: 120px;
      padding: 12px;
      resize: vertical;
    }
    .actions {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin-top: 18px;
    }
    button {
      min-height: 42px;
      border: 0;
      border-radius: 6px;
      padding: 0 18px;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }
    button.primary {
      background: #1f6feb;
      color: #fff;
    }
    button.secondary {
      background: #e9eef6;
      color: #172033;
    }
    #status {
      min-height: 24px;
      margin-top: 18px;
      color: #2b6b3f;
      line-height: 1.5;
      white-space: pre-line;
    }
    audio {
      width: 100%;
      margin-top: 12px;
    }
    code {
      background: #f1f4f9;
      padding: 2px 5px;
      border-radius: 4px;
    }
  </style>
</head>
<body>
  <main>
    <h1>数字人音色控制台</h1>
    <p>这里切换的是 Python 中转服务的默认 TTS 音色。UE5 如果不额外传 <code>voice</code>，会自动使用这里选中的音色。</p>

    <label for="voice">女性音色</label>
    <select id="voice">__VOICE_OPTIONS__</select>

    <label for="text">试听文本</label>
    <textarea id="text">你好，我是你的数字人助手。现在可以切换不同的女性音色了。</textarea>

    <div class="actions">
      <button class="primary" id="save">保存当前音色</button>
      <button class="secondary" id="preview">试听当前音色</button>
      <button class="secondary" id="health">检查服务状态</button>
    </div>

    <div id="status"></div>
    <audio id="player" controls hidden></audio>
  </main>

  <script>
    const voiceEl = document.getElementById('voice');
    const textEl = document.getElementById('text');
    const statusEl = document.getElementById('status');
    const playerEl = document.getElementById('player');

    async function loadVoices() {
      const res = await fetch('/voices');
      const data = await res.json();
      voiceEl.value = data.current_voice;
      statusEl.textContent = `当前音色：${data.current_voice}`;
    }

    async function saveVoice() {
      const voice = voiceEl.value;
      const res = await fetch('/set_voice', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ voice })
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.message || '保存失败');
      }
      statusEl.textContent = `已切换为：${data.current_voice}`;
    }

    async function previewVoice() {
      await saveVoice();
      statusEl.textContent = '正在合成试听音频...';
      const res = await fetch('/generate_voice', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: textEl.value,
          voice: voiceEl.value
        })
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.message || '合成失败');
      }
      playerEl.src = data.audio_url;
      playerEl.hidden = false;
      playerEl.play();

      const timing = data.timing || {};
      statusEl.textContent =
        `试听完成：${data.voice}\\n` +
        `TTS：${timing.tts ?? '-'} 秒，下载：${timing.download ?? '-'} 秒，转换：${timing.convert ?? '-'} 秒，总计：${timing.total ?? '-'} 秒`;
    }

    async function checkHealth() {
      const res = await fetch('/health');
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.message || '服务异常');
      }
      statusEl.textContent =
        `服务运行正常\\n` +
        `当前音色：${data.current_voice}\\n` +
        `音频目录：${data.tts_output_dir}`;
    }

    document.getElementById('save').addEventListener('click', () => {
      saveVoice().catch(err => {
        statusEl.textContent = err.message;
      });
    });

    document.getElementById('preview').addEventListener('click', () => {
      previewVoice().catch(err => {
        statusEl.textContent = err.message;
      });
    });

    document.getElementById('health').addEventListener('click', () => {
      checkHealth().catch(err => {
        statusEl.textContent = err.message;
      });
    });

    loadVoices().catch(err => {
      statusEl.textContent = err.message;
    });
  </script>
</body>
</html>"""

    return html.replace("__VOICE_OPTIONS__", voice_options)


# ============================================================
# 启动 Flask 服务
# ============================================================

if __name__ == "__main__":
    try:
        with urlopen("http://127.0.0.1:5000/health", timeout=2) as response:
            if response.status == 200:
                print("数字人 ASR / TTS 后端服务已经在运行，本次启动自动退出。", flush=True)
                sys.exit(0)
    except Exception:
        pass

    print("========================================", flush=True)
    print("数字人 ASR / TTS 后端服务启动中", flush=True)
    print("服务地址：http://127.0.0.1:5000", flush=True)
    print("健康检查：http://127.0.0.1:5000/health", flush=True)
    print("音频目录：", TTS_OUTPUT_DIR, flush=True)
    print("当前默认音色：", TTS_VOICE, flush=True)
    print("========================================", flush=True)

    # 推荐使用 waitress。没安装的话自动回退到 Flask 自带开发服务器。
    try:
        from waitress import serve

        print("使用 Waitress 启动服务", flush=True)

        serve(
            app,
            host="127.0.0.1",
            port=5000,
            threads=4
        )

    except ImportError:
        print("未安装 waitress，使用 Flask 自带服务器启动", flush=True)
        print("如需更稳定，可运行：pip install waitress", flush=True)

        app.run(
            host="127.0.0.1",
            port=5000,
            debug=False,
            threaded=True,
            use_reloader=False
        )
