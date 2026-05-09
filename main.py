import os
import re
import time
import json
import datetime
import requests

from github import Github
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from playwright_stealth import Stealth

# =========================================================
# CONFIG ĐA KÊNH
# =========================================================

CHANNELS = [
    {
        "id": "buncha",
        "name": "Bún Chả TV",
        "url": "https://bunchatv4.net/truc-tiep-bong-da-xoilac-tv",
        "base_url": "https://bunchatv4.net"
    },
    {
        "id": "hoiquan",
        "name": "Hội Quán TV",
        "url": "https://sv2.hoiquan3.live/lich-thi-dau/bong-da",
        "base_url": "https://sv2.hoiquan3.live"
    }
]

FILE_PATH = "bongda.m3u"  # Đổi thành đuôi .m3u
WAITING_VIDEO_URL = "https://example.com/waiting.mp4"
LIMIT_MATCHES = 15  # Tăng giới hạn để không bỏ lỡ trận đấu

VN_TZ = datetime.timezone(datetime.timedelta(hours=7))

GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME = os.getenv("GH_REPO", "Eternal161/dausoco")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}

LOGO_CACHE = {}

# =========================================================
# LOGO & UTILS
# =========================================================

def normalize_team_name(name):
    name = re.sub(r"\bFc\b$", "FC", name)
    return name.strip()

def get_team_logo(team_name):
    if not team_name or team_name == "Unknown":
        return ""
    team_name = normalize_team_name(team_name)
    if team_name in LOGO_CACHE:
        return LOGO_CACHE[team_name]
    try:
        slug = team_name.lower().replace(" ", "-")
        url = f"https://football-logos.cc/{slug}/"
        r = requests.get(url, headers=_HEADERS, timeout=3)
        match = re.search(r'https://football-logos.cc/logos/[^"]+\.png', r.text)
        if match:
            logo = match.group(0)
            LOGO_CACHE[team_name] = logo
            return logo
    except:
        pass
    return f"https://ui-avatars.com/api/?name={requests.utils.quote(team_name[:2])}&size=200&background=1565C0&color=ffffff&bold=true"

def parse_url_to_info(url):
    try:
        parts = url.rstrip('/').split('/')
        slug = ""
        for p in reversed(parts):
            if "-vs-" in p:
                slug = p.split('?')[0].split('#')[0]
                break
        if not slug:
            return "Unknown", "Unknown", "Chưa có lịch"
        slug = re.sub(r'-\d{6,}$', '', slug)
        time_match = re.search(r"-(\d{4}-\d{2}-\d{2}-\d{4})$", slug)
        if time_match:
            t = time_match.group(1)
            thoi_gian = f"{t[0:2]}:{t[2:4]} {t[5:7]}/{t[8:10]}/{t[11:15]}"
            teams_slug = slug[:slug.rfind("-" + t)]
        else:
            thoi_gian = "Unknown"
            teams_slug = slug
        teams = teams_slug.split("-vs-", 1)
        doi_nha = teams[0].replace("-", " ").title().strip()
        doi_khach = teams[1].replace("-", " ").title().strip() if len(teams) > 1 else "Unknown"
        return doi_nha, doi_khach, thoi_gian
    except:
        return "Unknown", "Unknown", "Unknown"

# =========================================================
# CAPTURE STREAM
# =========================================================

def capture_stream(context, match_url):
    page = context.new_page()
    Stealth().apply_stealth_sync(page)
    streams = set()

    def process_url(url):
        u = url.lower()
        if any(bad in u for bad in [".mp4", ".jpg", ".png", "waiting", "loop", "saba.m3u8", "/ad/", "/ads/", "quangcao"]):
            return
        if (".m3u8" in u or "taoxanh.biz" in u or "rapidlive.shop" in u or "100ycdn.com" in u or "hqtv" in u):
            streams.add(url)

    page.on("request", lambda req: process_url(req.url))
    page.on("response", lambda res: process_url(res.url))

    try:
        page.goto(match_url, wait_until="load", timeout=45000)
        page.wait_for_timeout(5000)
        # Click giả lập để kích hoạt player
        try:
            page.mouse.click(500, 500)
        except: pass

        deadline = time.time() + 12
        while time.time() < deadline:
            if any("wssession=" in s.lower() or "100ycdn" in s.lower() for s in streams):
                break
            time.sleep(1)
    except:
        pass
    finally:
        page.close()

    if streams:
        priority = []
        for s in streams:
            score = 0
            low = s.lower()
            if "100ycdn.com" in low: score += 6000
            if "hqtv" in low: score += 5000
            if "rapidlive.shop" in low: score += 4000
            if any(k in low for k in ["sign=", "token=", "wssession="]): score += 1000
            priority.append((score, s))
        priority.sort(reverse=True, key=lambda x: x[0])
        return priority[0][1]
    return None

# =========================================================
# EXPORT M3U
# =========================================================

def export_m3u(all_channel_data):
    """Gom tất cả trận đấu từ các kênh vào 1 file M3U duy nhất"""
    lines = ["#EXTM3U"]
    
    for channel_id in all_channel_data:
        channel_name = "Bún Chả TV" if channel_id == "buncha" else "Hội Quán TV"
        matches = all_channel_data[channel_id]
        
        for m in matches:
            # Chỉ xuất những trận có link stream thực sự
            if m["stream_url"] and m["stream_url"] != WAITING_VIDEO_URL:
                logo = m["logo_nha"]
                title = m["title"]
                time_str = m["thoi_gian"]
                status = m["trang_thai"]
                url = m["stream_url"]
                
                # Cấu trúc M3U chuẩn cho ứng dụng IPTV
                info = f'#EXTINF:-1 tvg-logo="{logo}" group-title="{channel_name}", {title} [{time_str}] - {status}'
                lines.append(info)
                lines.append(url)
                
    return "\n".join(lines)

# =========================================================
# PUSH GITHUB
# =========================================================

def push_to_github(content):
    if not GITHUB_TOKEN:
        with open(FILE_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"✅ Đã lưu file cục bộ: {FILE_PATH}")
        return

    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(REPO_NAME)
    msg = f"⚽ Update Playlist M3U: {datetime.datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}"

    try:
        contents = repo.get_contents(FILE_PATH)
        repo.update_file(contents.path, msg, content, contents.sha)
        print("✅ Đã cập nhật file trên GitHub")
    except:
        repo.create_file(FILE_PATH, msg, content)
        print("✅ Đã tạo file mới trên GitHub")

# =========================================================
# MAIN PROCESS
# =========================================================

def scrape_and_push():
    all_channel_data = {"buncha": [], "hoiquan": []}
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 720}, user_agent=_HEADERS["User-Agent"])

        for ch in CHANNELS:
            print(f"\n🔍 Quét danh sách: {ch['name']}")
            page = context.new_page()
            Stealth().apply_stealth_sync(page)
            
            try:
                page.goto(ch["url"], wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(3000)
                # Cuộn trang để load thêm trận
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(2000)

                # Lấy tất cả các thẻ <a> có chứa trận đấu
                found_links = page.locator("a[href*='-vs-']").all()
                links = []
                seen = set()
                for l in found_links:
                    href = l.get_attribute("href")
                    if href and href not in seen:
                        full_url = href if href.startswith("http") else ch["base_url"].rstrip('/') + '/' + href.lstrip('/')
                        links.append(full_url)
                        seen.add(href)
                
                links = links[:LIMIT_MATCHES]
                print(f"✅ Tìm thấy {len(links)} link tiềm năng")

                for idx, href in enumerate(links):
                    doi_nha, doi_khach, thoi_gian = parse_url_to_info(href)
                    
                    # Xác định trạng thái Live
                    is_live = False
                    status = "Chưa đá ⏳"
                    try:
                        match_time = datetime.datetime.strptime(thoi_gian, "%H:%M %d/%m/%Y").replace(tzinfo=VN_TZ)
                        diff = (datetime.datetime.now(VN_TZ) - match_time).total_seconds() / 60
                        if -15 <= diff <= 130:
                            is_live = True
                            status = "Đang trực tiếp 🔴"
                    except: pass

                    match_obj = {
                        "title": f"{doi_nha} vs {doi_khach}",
                        "thoi_gian": thoi_gian,
                        "trang_thai": status,
                        "is_live": is_live,
                        "logo_nha": get_team_logo(doi_nha),
                        "stream_url": WAITING_VIDEO_URL,
                        "link_xem": href
                    }
                    all_channel_data[ch["id"]].append(match_obj)
            except Exception as e:
                print(f"❌ Lỗi quét kênh {ch['name']}: {e}")
            finally:
                page.close()

        # Tiến hành bắt luồng cho các trận đang Live
        print("\n🚀 Bắt đầu lấy link stream thực tế...")
        for ch_id in all_channel_data:
            live_list = [m for m in all_channel_data[ch_id] if m["is_live"]]
            for m in live_list:
                print(f"📡 Đang bắt luồng: {m['title']}")
                stream = capture_stream(context, m["link_xem"])
                if stream:
                    m["stream_url"] = stream
                    print(f"   ✨ Thành công!")

        browser.close()

    # Xuất file M3U và đẩy lên GitHub
    m3u_final = export_m3u(all_channel_data)
    push_to_github(m3u_final)

if __name__ == "__main__":
    scrape_and_push()
