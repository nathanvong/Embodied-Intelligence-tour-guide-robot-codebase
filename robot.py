#!/usr/bin/env python3.8
import requests
import rospy
import cv2
from cv_bridge import CvBridge
import re
import numpy as np
import pygame
import io
import threading

# 匯入您自己的機器人底盤函式庫
from RobotChassis import RobotChassis 

# ROS 訊息類型
from geometry_msgs.msg import Twist
from sensor_msgs.msg import Image, Imu
from mr_voice.msg import Voice
from std_msgs.msg import String
from tf.transformations import euler_from_quaternion

# --- 設定 ---
COMPUTER_SERVER_URL = "http://192.168.50.174:5000/request_message"
CONFIRMATION_TIMEOUT = 15

# --- 全域變數 ---
_image = None
_voice = None
chassis = None # 將 chassis 設為全域變數

# --- 狀態機變數 ---
STATE_IDLE = 0
STATE_WAITING_FOR_CONFIRMATION = 1
current_state = STATE_IDLE
pending_navigation_response = None
confirmation_timer = 0

# --- 展品座標對應表 ---
EXHIBIT_COORDS = {
    '1': (1.0, 1.0, 0.0),
    '2': (2.0, 2.0, 0.0),
    '3': (3.0, 3.0, 0.0),
    '4': (4.0, 4.0, 0.0),
    '5': (5.0, 5.0, 0.0)
}

# --- 初始化 ---
rospy.init_node("robot_ai_client_interactive")
pygame.mixer.init()

# --- 回呼函式 ---
def callback_voice(msg):
    global _voice
    _voice = msg
    rospy.loginfo(f"接收到語音: '{msg.text}'")

def callback_image(msg):
    global _image
    _image = CvBridge().imgmsg_to_cv2(msg, "bgr8")

# --- 機器人動作與語音 ---
def say(text):
    rospy.loginfo(f"準備朗讀 (TTS): {text}")
    try:
        url = "https://translate.google.com/translate_tts"
        params = {"ie": "UTF-8", "q": text, "tl": "yue", "client": "tw-ob"}
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code == 200:
            audio_file = io.BytesIO(response.content)
            pygame.mixer.music.load(audio_file)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                rospy.sleep(0.1)
    except Exception as e:
        rospy.logwarn(f"Google TTS 失敗: {e}.")

# --- 全新的導航處理函式 ---
def _execute_and_monitor_nav(x, y, theta):
    """
    在背景執行緒中執行並監控導航任務的函式。
    """
    global chassis
    if chassis is None:
        rospy.logerr("底盤尚未初始化！")
        return

    rospy.loginfo(f"開始導航至 ({x}, {y}, {theta})")
    chassis.move_to(x, y, theta)
    
    # 進入監控迴圈
    rate = rospy.Rate(5) # 每秒檢查 5 次狀態
    while not rospy.is_shutdown():
        code = chassis.status_code
        text = chassis.status_text
        rospy.loginfo(f"導航狀態: Code={code}, Text='{text}'")

        if code == 1: # 1: 接收成功
            # 可以選擇在這裡說「已收到目標」
            pass
        elif code == 2: # 2: 導航中
            # 導航進行中，繼續等待
            pass
        elif code == 3: # 3: 完成
            rospy.loginfo("導航成功完成！")
            say("我哋到咗喇！呢度就係你要睇嘅展品。")
            break # 退出監控迴圈
        elif code >= 4: # >=4: 錯誤
            rospy.logerr("導航發生錯誤！")
            say("哎呀，導航出錯喇，我迷路喇。")
            break # 退出監控迴圈
        
        rate.sleep()

def handle_navigation(response_text):
    """
    解析指令，並啟動一個新的執行緒來處理導航。
    """
    match = re.search(r"編號係(\d+)號", response_text)
    if not match:
        rospy.loginfo("回應中未找到導航指令或展品編號。")
        return

    exhibit_id = match.group(1)
    
    if exhibit_id in EXHIBIT_COORDS:
        coords = EXHIBIT_COORDS[exhibit_id]
        # 創建並啟動導航執行緒，以避免阻塞主循環
        nav_thread = threading.Thread(target=_execute_and_monitor_nav, args=coords)
        nav_thread.daemon = True
        nav_thread.start()
    else:
        rospy.logwarn(f"未知的展品編號: {exhibit_id}，無法找到對應座標。")

# --- 主程式 ---
if __name__ == "__main__":
    global chassis
    chassis = RobotChassis() # 初始化底盤物件

    rospy.Subscriber("/voice/text", Voice, callback_voice)
    rospy.Subscriber("/camera/rgb/image_raw", Image, callback_image)
    
    rospy.loginfo("等待 3 秒確保連接穩定...")
    rospy.sleep(3)
    rospy.loginfo("系統就緒。")
    say("我準備好啦")
   
    while not rospy.is_shutdown():
        # 處理超時 (邏輯不變)
        if current_state == STATE_WAITING_FOR_CONFIRMATION and rospy.get_time() > confirmation_timer:
            rospy.loginfo("等待確認超時，返回閒置狀態。")
            say("睇嚟你冇指令，咁我繼續等喇。")
            current_state = STATE_IDLE
            pending_navigation_response = None
        
        # 處理語音指令 (邏輯不變)
        if _voice is not None:
            text = _voice.text.strip()
            local_voice_message = _voice
            _voice = None

            if current_state == STATE_IDLE:
                if "機器人" in text or "機械人" in text:
                    if _image is not None:
                        current_image = _image.copy()
                        _, img_encoded = cv2.imencode('.jpg', current_image)
                        files = {'photo': ('image.jpg', img_encoded.tobytes(), 'image/jpeg')}
                        data = {'text': text}
                        try:
                            response = requests.post(COMPUTER_SERVER_URL, files=files, data=data, timeout=60)
                            if response.status_code == 200:
                                response_text = response.text
                                say(response_text)
                                if re.search(r"編號係(\d+)號", response_text):
                                    pending_navigation_response = response_text
                                    current_state = STATE_WAITING_FOR_CONFIRMATION
                                    confirmation_timer = rospy.get_time() + CONFIRMATION_TIMEOUT
                            else:
                                say("對唔住，電腦伺服器好似有啲問題。")
                        except requests.exceptions.RequestException:
                            say("哎呀，網絡斷咗線，我連唔到電腦喇。")
            
            elif current_state == STATE_WAITING_FOR_CONFIRMATION:
                CONFIRMATION_WORDS = ["好", "要", "可以", "ok", "yes", "帶我過去"]
                has_trigger = "機器人" in text or "機械人" in text
                has_confirmation = any(word in text for word in CONFIRMATION_WORDS)

                if has_trigger and has_confirmation:
                    rospy.loginfo("收到雙重確認指令，開始導航。")
                    say("好嘅，我即刻帶你過去。")
                    # *** 呼叫新的導航啟動函式 ***
                    handle_navigation(pending_navigation_response)
                    current_state = STATE_IDLE
                    pending_navigation_response = None
                
                elif has_trigger and not has_confirmation:
                    rospy.loginfo("收到新的觸發指令，取消當前導航。")
                    say("收到，已取消之前的導航。")
                    current_state = STATE_IDLE
                    pending_navigation_response = None
                    _voice = local_voice_message
                
                else:
                    rospy.loginfo("聽到了無關語音，繼續等待確認...")

        rospy.sleep(0.1)