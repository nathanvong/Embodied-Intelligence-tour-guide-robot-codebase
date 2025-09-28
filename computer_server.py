import os
import threading
import logging
import base64
import json
import time
from flask import Flask, request, jsonify, render_template, url_for
import google.generativeai as genai

GEMINI_API_KEY = "your_api_key"

# --- Flask App 初始化 ---
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
app.logger.setLevel(logging.INFO)

# --- 全域變數，用於儲存最新狀態給網頁監控 ---
latest_data = {
    "robot_image_url": None,
    "prompt_text": "等待機器人指令...",
    "reasoning": None,
    "recommendation": None,
    "timestamp": None
}
data_lock = threading.Lock()

# --- Gemini AI 初始化 ---
chat = None
try:
    genai.configure(api_key=GEMINI_API_KEY)
    with open("prompt.txt", "r", encoding="UTF-8") as f:
        system_instruction = f.read()
    model = genai.GenerativeModel(model_name="gemini-2.5-flash", system_instruction=system_instruction)
    chat = model.start_chat()
    app.logger.info("Gemini 模型 'gemini-2.5-flash' 成功載入。")
except Exception as e:
    app.logger.error(f"無法設定 Gemini: {e}")

# --- 展品資料庫 (與機器人端同步) ---
EXHIBITS = {
    "多級齒輪傳動系統": {"id": 1, "image": "exhibit_gears.png"},
    "宋太祖蹴鞠圖": {"id": 2, "image": "exhibit_cuju.png"},
    "商代甲骨文骨片": {"id": 3, "image": "exhibit_oracle.png"},
    "蒙娜麗莎": {"id": 4, "image": "exhibit_monalisa.png"},
    "互動式地球儀": {"id": 5, "image": "exhibit_globe.png"}
}


# --- Flask 路由 ---

@app.route('/')
def index():
    """提供監控網頁介面"""
    return render_template('interface.html')


@app.route('/get_latest_data')
def get_latest_data():
    """讓監控網頁獲取最新數據的 API"""
    with data_lock:
        return jsonify(latest_data)


@app.route('/request_message', methods=['POST'])
def handle_robot_request():
    """接收機器人請求並使用 Gemini 處理的核心 API"""
    global latest_data

    if not model:  # 我們檢查 model 而不是 chat
        return "Gemini 模型未初始化", 500

    # !!! 核心修改：為每一次請求都建立一個全新的 chat 會話 !!!
    chat = model.start_chat()

    # 1. 接收來自機器人的資料
    prompt_text = request.form.get('text')
    image_file = request.files.get('photo')

    if not prompt_text or not image_file:
        return "缺少文字或圖片資料", 400

    app.logger.info(f"收到機器人請求: text='{prompt_text}'")

    # 2. 處理並儲存圖片，供網頁顯示
    image_bytes = image_file.read()
    image_path = os.path.join('static', 'uploads', 'latest_image.jpg')
    os.makedirs(os.path.dirname(image_path), exist_ok=True)
    with open(image_path, 'wb') as f:
        f.write(image_bytes)

    # 3. 呼叫 Gemini AI
    try:
        image_part = {"mime_type": "image/jpeg", "data": base64.b64encode(image_bytes).decode('utf-8')}

        # --- 任務 1: 觀察與分析 ---
        # (此處及之後的 AI 邏輯完全不變)
        observation_prompt = f"訪客問題: '{prompt_text}'。請根據訪客影像和問題，執行你的工作流程『任務1』，返回包含 observations 和 labels 的 JSON 物件。"
        response_observation = chat.send_message([observation_prompt, image_part])
        app.logger.info(f"Gemini 原始回應 (任務1): '{response_observation.text}'")
        cleaned_observation_text = response_observation.text.strip().replace("json", "").replace("", "")
        if not cleaned_observation_text:
            raise ValueError("Gemini 任務1返回了空回應")
        reasoning_data = json.loads(cleaned_observation_text)

        # --- 任務 2: 推薦 ---
        generated_labels = reasoning_data.get("labels", [])
        if not generated_labels:
            raise ValueError("AI 未能產生有效的興趣標籤。")
        recommend_prompt = f"訪客嘅興趣標籤係: {', '.join(generated_labels)}。請執行你的工作流程『任務2』，推薦一個展品，並以指定的JSON格式返回。"
        response_recommend = chat.send_message(recommend_prompt)
        app.logger.info(f"Gemini 原始回應 (任務2): '{response_recommend.text}'")
        cleaned_recommend_text = response_recommend.text.strip().replace("json", "").replace("", "")
        if not cleaned_recommend_text:
            raise ValueError("Gemini 任務2返回了空回應")
        recommendation_data = json.loads(cleaned_recommend_text)

        # ... 後續的所有程式碼 (準備回傳、更新latest_data等) 都維持不變 ...
        exhibit_name = recommendation_data.get("exhibit_name")
        reason = recommendation_data.get("reason")
        if exhibit_name not in EXHIBITS:
            raise ValueError(f"Gemini 推薦了一個不存在的展品: {exhibit_name}")
        exhibit_info = EXHIBITS[exhibit_name]
        final_response_to_robot = f"{reason} 要唔要我帶你過去睇下呀？呢個展品嘅編號係{exhibit_info['id']}號。"
        app.logger.info(f"準備回傳給機器人: '{final_response_to_robot}'")

        with data_lock:
            recommendation_data['exhibit_image_url'] = url_for('static', filename=f'uploads/{exhibit_info["image"]}')
            latest_data = {
                "robot_image_url": f"{image_path}?t={os.path.getmtime(image_path)}",
                "prompt_text": prompt_text,
                "reasoning": reasoning_data,
                "recommendation": recommendation_data,
                "final_response": final_response_to_robot,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
            }
        return final_response_to_robot

    except Exception as e:
        app.logger.error(f"處理請求時發生錯誤: {str(e)}")
        error_message = "對唔住，我諗嘢有啲混亂，可唔可以再問一次？"
        with data_lock:
            latest_data['reasoning'] = {"error": str(e)}
            latest_data['recommendation'] = None
            latest_data['final_response'] = error_message
        return error_message, 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)