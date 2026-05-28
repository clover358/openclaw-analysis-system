import os
import json
import time
import queue
import threading
import hashlib
from playwright.sync_api import sync_playwright

# ================= 配置区 =================
SAVE_DIR = r"D:\Agent\openclaw-analysis-system\data"
os.makedirs(SAVE_DIR, exist_ok=True)
MERGED_FILE_PATH = os.path.join(SAVE_DIR, "merged_rankings.json")

# ================= 全局状态 =================
data_queue = queue.Queue()
collected_fingerprints = set() 
agent_running = True
sequential_chart_count = 0     
# ==========================================

def get_data_fingerprint(data):
    try:
        if isinstance(data, dict):
            for key, val in data.items():
                if key in ['a', 'f', 'q', 'i', 'cachedAt']: 
                    continue
                if isinstance(val, list) and len(val) > 0:
                    first_item_str = json.dumps(val[0], sort_keys=True)
                    return hashlib.md5(first_item_str.encode()).hexdigest()
                elif isinstance(val, dict) and len(val) > 0:
                    first_k = next(iter(val))
                    first_item_str = json.dumps({first_k: val[first_k]}, sort_keys=True)
                    return hashlib.md5(first_item_str.encode()).hexdigest()
        elif isinstance(data, list) and len(data) > 0:
            first_item_str = json.dumps(data[0], sort_keys=True)
            return hashlib.md5(first_item_str.encode()).hexdigest()
    except Exception:
        pass
    return hashlib.md5(str(data)[:500].encode()).hexdigest()

def classify_and_name_data(data, current_view):
    global sequential_chart_count
    
    if isinstance(data, dict) and "day" in data and "week" in data and "month" in data:
        return "Top Apps.json"
        
    if isinstance(data, dict) and "data" in data and isinstance(data["data"], list) and len(data["data"]) > 0:
        if "x" in data["data"][0] and "ys" in data["data"][0]:
            return "Top Model.json"
            
    if isinstance(data, list) and len(data) > 0 and "model_permaslug" in data[0]:
        return f"{current_view}_leaderboard.json"
        
    if isinstance(data, list) and len(data) > 0 and "x" in data[0] and "ys" in data[0]:
        ys_keys = list(data[0]["ys"].keys())
        
        if "openai" in ys_keys or "google" in ys_keys or "anthropic" in ys_keys:
            return "Market Share.json"
        else:
            sequential_names = ["Categories.json", "Languages.json", "Programming.json", "Context Length.json"]
            if sequential_chart_count < 4:
                file_name = sequential_names[sequential_chart_count]
                sequential_chart_count += 1
                return file_name
            else:
                return f"Extra_Chart_{sequential_chart_count}.json"

    return None

def parse_rsc_text(text_data):
    lines = text_data.strip().split('\n')
    for line in lines:
        colon_idx = line.find(':')
        if colon_idx != -1:
            json_str = line[colon_idx+1:]
            try:
                data = json.loads(json_str)
                if len(str(data)) > 100: 
                    return data
            except json.JSONDecodeError:
                continue
    return None

def data_processor_thread():
    global agent_running
    
    print("👷 数据处理大脑已启动，等待特征匹配...")
    while agent_running:
        try:
            current_view, raw_text = data_queue.get(timeout=1)
            json_data = parse_rsc_text(raw_text)
            
            if json_data:
                fingerprint = get_data_fingerprint(json_data)
                
                if fingerprint not in collected_fingerprints:
                    file_name = classify_and_name_data(json_data, current_view)
                    
                    if file_name:
                        collected_fingerprints.add(fingerprint)
                        file_path = os.path.join(SAVE_DIR, file_name)
                        
                        with open(file_path, "w", encoding="utf-8") as f:
                            json.dump(json_data, f, ensure_ascii=False, indent=2)
                        
                        print(f"🎯 [命中目标] 成功捕捉并保存: {file_name}")
            
            data_queue.task_done()
        except queue.Empty:
            continue
        except Exception as e:
            print(f"❌ 数据处理报错: {e}")

def merge_json_files():
    print("\n📦 开始合并采集到的榜单数据...")
    merged_data = {}
    
    for file_name in os.listdir(SAVE_DIR):
        if file_name.endswith(".json") and file_name != "merged_rankings.json":
            file_path = os.path.join(SAVE_DIR, file_name)
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    key_name = file_name.replace(".json", "")
                    merged_data[key_name] = json.load(f)
            except Exception as e:
                 print(f"⚠️ 读取 {file_name} 失败: {e}")
                 
    with open(MERGED_FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(merged_data, f, ensure_ascii=False, indent=4)
        
    print(f"🎉 终极数据合并完成！总共合并了 {len(merged_data)} 个维度。文件已保存至:\n   {MERGED_FILE_PATH}")

def run_agent():
    global agent_running
    
    processor = threading.Thread(target=data_processor_thread)
    processor.start()

    with sync_playwright() as p:
        print("🔗 智能体正在连接浏览器底层通信接口...")
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]
        page = context.new_page()
        page.set_default_navigation_timeout(120000)

        current_view_state = {"view": "week"}

        def receive_data(data_str):
            data_queue.put((current_view_state["view"], data_str))

        page.expose_function("sendDataToAgent", receive_data)

        stealth_js = """
        const originalFetch = window.fetch;
        window.fetch = async function(...args) {
            const response = await originalFetch.apply(this, args);
            const url = typeof args[0] === 'string' ? args[0] : (args[0]?.url || '');
            
            if (url.includes("rankings") || url.includes("_rsc")) {
                response.clone().text().then(text => {
                    if (text.length > 10000) {
                        window.sendDataToAgent(text); 
                    }
                }).catch(e => {});
            }
            return response;
        };
        """
        page.add_init_script(stealth_js)

        target_tasks = [
            {"url": "https://openrouter.ai/rankings", "view": "week"},
            {"url": "https://openrouter.ai/rankings?view=day", "view": "day"},
            {"url": "https://openrouter.ai/rankings?view=month", "view": "month"}
        ]

        for task in target_tasks:
            current_view_state["view"] = task["view"]
            print(f"\n🚀 智能体正在前往: {task['url']}")
            try:
                page.goto(task["url"], wait_until="commit")
                
                # 【修改点 1】采纳你的强刷策略！第一次进入时强制刷新，打破 HTML 预加载
                if task["view"] == "week":
                    page.wait_for_timeout(2000)
                    print("🔄 正在执行强制刷新，唤醒底层 fetch 请求...")
                    page.reload(wait_until="commit")
                
                page.wait_for_timeout(3000)
                
                print("=======================================================")
                print(f"🟢 已加载 {task['view']} 视图！")
                print("👉 【请在浏览器中手动向下滚动网页】，触发所有的榜单数据加载。")
                print("👉 看到终端显示成功保存了你需要的榜单后，在这里按回车键！")
                print("=======================================================")
                
                # 【修改点 2】无阻塞等待：使用多线程监控回车，释放 Playwright 的事件循环
                print("⌨️ 滚动完成后，请在这里按【回车键】继续...")
                enter_pressed = [False]
                def wait_for_enter():
                    input()
                    enter_pressed[0] = True
                    
                threading.Thread(target=wait_for_enter, daemon=True).start()
                
                # 在你按回车之前，这里每 0.2 秒轮询一次，让拦截器的数据能实时推送到 Python
                while not enter_pressed[0]:
                    page.wait_for_timeout(200) 
                
            except Exception as e:
                print(f"⚠️ 访问 {task['url']} 时出现异常: {e}")

        print("\n🛑 所有维度的采集任务结束，正在清理现场...")
        page.close()
        agent_running = False
        processor.join()
        
        merge_json_files()

if __name__ == "__main__":
    run_agent()