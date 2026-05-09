import os
import re
import time
import json
import datetime
import requests
from github import Github
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

# ==========================================
# CẤU HÌNH
# ==========================================
TARGET_SITE = "https://bunchatv4.net/truc-tiep-bong-da-xoilac-tv"
GITHUB_TOKEN = os.getenv("GH_TOKEN")
REPO_NAME    = os.getenv("GH_REPO", "Eternal161/dausoco")
FILE_PATH    = "bongda.json"
WAITING_VIDEO_URL = "https://example.com/video-cho.mp4"
LIMIT_MATCHES     = 10
MAX_STREAM_WAIT   = 45
STREAM_POLL_INTERVAL = 1

VN_TZ = datetime.timezone(datetime.timedelta(hours=7))

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
}

# ==========================================
# PARSE DANH SÁCH TRẬN TỪ HTML (không dùng Playwright locator)
# ==========================================
# Logo trên trang được nhúng trực tiếp trong HTML dạng:
#   <img ... src="https://cdn-live.taoxanh.biz/live-dev/football/team/logo/abc.png" ...>
# Nằm trong thẻ <a href="/truc-tiep/...">

_LOGO_CDN_DOMAINS = [
    "cdn-live.taoxanh.biz",
    "cdn.rapid-api.icu",
    "bunchatv4.net",
]

def _is_team_logo_url(url: str) -> bool:
    """Kiểm tra URL có phải logo đội bóng thật không."""
    u = url.lower()
    # Phải thuộc CDN đã biết
    if not any(d in u for d in _LOGO_CDN_DOMAINS):
        return False
    # Loại ảnh background, category, avatar
    bad = ["/categories/", "header", "logo.svg", "earth.png",
           "user_avatar", "header-mobi"]
    return not any(b in u for b in bad)


def parse_match_block(html_block: str, base_url: str) -> dict | None:
    """
    Parse 1 block HTML của 1 trận đấu.
    Trả về dict hoặc None nếu lỗi.
    """
    try:
        # Lấy href
        href_m = re.search(r'href="(/truc-tiep/[^"]+)"', html_block)
        if not href_m:
            return None
        href = base_url + href_m.group(1)

        # Lấy tất cả img src trong block
        img_srcs = re.findall(r'<img[^>]+src="([^"]+)"', html_block)
        # Lọc lấy logo đội
        team_logos = [s for s in img_srcs if _is_team_logo_url(s)]

        logo_nha   = team_logos[0] if len(team_logos) >= 1 else ""
        logo_khach = team_logos[1] if len(team_logos) >= 2 else ""

        # Lấy tên đội từ alt của img logo
        # dạng: <img ... alt="Liverpool" ...>
        alt_m = re.findall(r'<img[^>]+alt="([^"]+)"[^>]*src="(?:' +
                           "|".join(_LOGO_CDN_DOMAINS) + r')[^"]*"', html_block)
        doi_nha   = alt_m[0].strip() if len(alt_m) >= 1 else ""
        doi_khach = alt_m[1].strip() if len(alt_m) >= 2 else ""

        # Fallback: parse tên từ URL nếu alt rỗng
        if not doi_nha or not doi_khach:
            fn, fk, _ = parse_url_to_info(href)
            if not doi_nha:
                doi_nha = fn
            if not doi_khach:
                doi_khach = fk

        # Lấy giờ thi đấu
        _, _, thoi_gian = parse_url_to_info(href)

        # Kiểm tra LIVE
        is_live = bool(re.search(r'(?i)\bLive\b|Đang trực tiếp', html_block))

        return {
            "href":       href,
            "doi_nha":    doi_nha,
            "doi_khach":  doi_khach,
            "logo_nha":   logo_nha,
            "logo_khach": logo_khach,
            "thoi_gian":  thoi_gian,
            "is_live":    is_live,
        }
    except Exception as e:
        print(f"⚠ parse_match_block lỗi: {e} - main.py:105")
        return None


def fetch_match_list_from_html(html: str, base_url: str) -> list[dict]:
    """
    Tách HTML thành từng block trận, parse từng block.
    Block = nằm giữa <a href="/truc-tiep/..."> ... </a>
    """
    # Tìm tất cả block <a href="/truc-tiep/..."> ... </a>
    blocks = re.findall(
        r'(<a\s+[^>]*href="/truc-tiep/[^"]*-vs-[^"]*"[^>]*>.*?</a>)',
        html, re.DOTALL
    )
    results = []
    seen = set()
    for block in blocks:
        parsed = parse_match_block(block, base_url)
        if parsed and parsed["href"] not in seen:
            seen.add(parsed["href"])
            results.append(parsed)
    return results


# ==========================================
# PARSE URL → THÔNG TIN TRẬN
# ==========================================
def parse_url_to_info(url: str) -> tuple[str, str, str]:
    try:
        m = re.search(r'/truc-tiep/([^/?#]+)', url)
        if not m:
            return "Unknown", "Unknown", "Chưa có lịch"
        slug = m.group(1)
        # Bỏ ID số cuối nếu có (601445470)
        slug = re.sub(r'/?\d{6,}$', '', slug).strip('/')
        t_m  = re.search(r'-(\d{4}-\d{2}-\d{2}-\d{4})$', slug)
        if t_m:
            t = t_m.group(1)
            thoi_gian  = f"{t[0:2]}:{t[2:4]} {t[5:7]}/{t[8:10]}/{t[11:15]}"
            teams_slug = slug[: slug.rfind('-' + t)]
        else:
            thoi_gian, teams_slug = "Chưa có lịch", slug
        parts     = teams_slug.split('-vs-', 1)
        doi_nha   = parts[0].replace('-', ' ').title().strip()
        doi_khach = parts[1].replace('-', ' ').title().strip() if len(parts) > 1 else "Unknown"
        return doi_nha, doi_khach, thoi_gian
    except Exception:
        return "Unknown", "Unknown", "Unknown"


# ==========================================
# BẮT LUỒNG M3U8
# ==========================================

# Từ khoá nhận dạng quảng cáo (chính xác, tránh false positive)
_AD_KEYWORDS = [
    "/vast/", "advertisement", "doubleclick.net",
    "googlesyndication", "quangcao", "preroll", "midroll",
    "ad-stream", "adserver",
]

_SKIP_SELECTORS = [
    ".skip-ad-btn", ".vast-skip-button", ".skip-button",
    "[class*='skip']", "[id*='skip']",
    "xpath=//*[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'bỏ qua')]",
    "xpath=//*[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'skip ad')]",
]


def _is_ad_url(url: str) -> bool:
    return any(kw in url.lower() for kw in _AD_KEYWORDS)


def _trigger_player_in_frame(frame) -> None:
    """Kích hoạt autoplay trong 1 frame."""
    try:
        frame.evaluate("""
            document.querySelectorAll('video').forEach(v => {
                v.muted = true; v.play().catch(() => {});
            });
        """)
    except Exception:
        pass
    for sel in [
        ".vjs-big-play-button", ".jw-icon-display",
        ".play-btn", ".play-wrapper", "[class*='play']",
    ]:
        try:
            el = frame.locator(sel).first
            if el.is_visible(timeout=500):
                el.click(timeout=500)
                break
        except Exception:
            pass


def _try_skip_ad(page) -> bool:
    for sel in _SKIP_SELECTORS:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1500):
                btn.click(timeout=1500)
                page.wait_for_timeout(1500)
                return True
        except Exception:
            pass
    return False


def capture_stream(context, match_url: str) -> str | None:
    page = context.new_page()
    streams:    list[str] = []
    ad_streams: set[str]  = set()

    def on_request(req):
        url = req.url
        u   = url.lower()
        if ".mp4" in u:
            return
        if ".m3u8" in u or ".flv" in u or "playlist" in u:
            if _is_ad_url(url):
                ad_streams.add(url)
            elif url not in streams:
                streams.append(url)
                print(f"      📶 Bắt được: {url[:90]}")

    try:
        page.on("request", on_request)

        # Anti-bot headers
        page.set_extra_http_headers({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        })

        page.goto(match_url, timeout=60_000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        # Kích hoạt player trong page chính
        _trigger_player_in_frame(page)
        try:
            vp = page.viewport_size
            if vp:
                page.mouse.click(vp["width"] / 2, vp["height"] / 2)
        except Exception:
            pass

        # Kích hoạt trong tất cả iframe
        for frame in page.frames:
            if frame != page.main_frame:
                _trigger_player_in_frame(frame)

        deadline         = time.time() + MAX_STREAM_WAIT
        skip_attempted   = False
        last_frame_count = len(page.frames)

        while time.time() < deadline:
            time.sleep(STREAM_POLL_INTERVAL)

            # Kích hoạt iframe mới load (lazy)
            cur = page.frames
            if len(cur) != last_frame_count:
                last_frame_count = len(cur)
                for frame in cur:
                    if frame != page.main_frame:
                        _trigger_player_in_frame(frame)

            if not skip_attempted and ad_streams:
                skip_attempted = True
                if _try_skip_ad(page):
                    print("      🔪 Skip quảng cáo, reset luồng...")
                    streams.clear()
                    ad_streams.clear()

            if streams:
                elapsed = MAX_STREAM_WAIT - (deadline - time.time())
                print(f"      ✅ Có luồng sau {elapsed:.0f}s")
                break

    except PWTimeout:
        print("      ⚠️  Timeout")
    except Exception as e:
        print(f"      ❌ Lỗi: {e}")
    finally:
        try:
            page.remove_listener("request", on_request)
        except Exception:
            pass
        page.close()

    live_streams = [s for s in streams if "live" in s.lower()]
    return (live_streams or streams or [None])[0]


# ==========================================
# TẠO JSON & PUSH LÊN GITHUB
# ==========================================
def create_json(matches_data: list) -> str:
    live_count   = sum(1 for m in matches_data if m.get("is_live"))
    stream_count = sum(1 for m in matches_data if m.get("stream_url") and m["stream_url"] != WAITING_VIDEO_URL)
    return json.dumps({
        "playlist_name": "Sáng TV",
        "last_updated":  datetime.datetime.now(VN_TZ).strftime("%H:%M %d/%m/%Y"),
        "total_live":    live_count,
        "total_streams": stream_count,
        "matches":       matches_data,
    }, indent=2, ensure_ascii=False)


def push_to_github(content: str, live: int, streams: int) -> None:
    if not GITHUB_TOKEN:
        print("⚠️  Không có GH_TOKEN, lưu local.")
        with open(FILE_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        return
    g    = Github(GITHUB_TOKEN)
    repo = g.get_repo(REPO_NAME)
    msg  = (f"⚽ Cập nhật: {datetime.datetime.now(VN_TZ).strftime('%H:%M %d/%m/%Y')}"
            f" — {live} live, {streams} streams")
    try:
        existing = repo.get_contents(FILE_PATH)
        repo.update_file(existing.path, msg, content, existing.sha)
        print(f"✅ Đã cập nhật GitHub: {FILE_PATH}")
    except Exception:
        repo.create_file(FILE_PATH, msg, content)
        print(f"✅ Đã tạo mới GitHub: {FILE_PATH}")


# ==========================================
# HÀM CHÍNH
# ==========================================
def scrape_and_push():
    matches_data = []
    print("=" * 65)
    print(f"⏰ BẮT ĐẦU: {datetime.datetime.now(VN_TZ).strftime('%H:%M:%S %d/%m/%Y')}")
    print("=" * 65)

    base_url = "/".join(TARGET_SITE.split("/")[:3])  # https://bunchatv4.net

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox", "--disable-setuid-sandbox",
                "--disable-dev-shm-usage", "--disable-gpu",
                "--autoplay-policy=no-user-gesture-required",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-blink-features=AutomationControlled",
                "--window-size=1920,1080",
            ],
        )
        ctx = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="vi-VN",
            timezone_id="Asia/Ho_Chi_Minh",
            java_script_enabled=True,
        )
        # Ẩn webdriver fingerprint
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins',   { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['vi-VN', 'vi', 'en-US'] });
            window.chrome = { runtime: {} };
        """)

        # ── BƯỚC 1: Lấy HTML trang danh sách ────────────────────────
        page = ctx.new_page()
        print("\n📋 BƯỚC 1: Tải trang danh sách trận...")
        try:
            page.goto(TARGET_SITE, timeout=60000)
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception as e:
            print(f"   ⚠️  Load chậm: {e}")

        # Scroll để lazy-load ảnh
        for _ in range(4):
            page.evaluate("window.scrollBy(0, 900)")
            page.wait_for_timeout(600)
        page.wait_for_timeout(1000)

        # ── FIX LOGO: Lấy HTML đầy đủ sau khi render ────────────────
        full_html = page.content()
        page.close()

        # ── BƯỚC 2: Parse HTML → danh sách trận + logo ──────────────
        print("\n📊 BƯỚC 2: Parse HTML lấy trận & logo...")
        parsed_list = fetch_match_list_from_html(full_html, base_url)

        if LIMIT_MATCHES:
            parsed_list = parsed_list[:LIMIT_MATCHES]
        print(f"   ✓ Tìm thấy {len(parsed_list)} trận")

        for i, item in enumerate(parsed_list):
            doi_nha   = item["doi_nha"]
            doi_khach = item["doi_khach"]
            thoi_gian = item["thoi_gian"]
            logo_nha  = item["logo_nha"]
            logo_khach= item["logo_khach"]
            is_live   = item["is_live"]

            # Tính lại is_live chính xác theo giờ VN
            status = "Đang trực tiếp 🔴" if is_live else "Chưa đá ⏳"
            try:
                match_time   = datetime.datetime.strptime(thoi_gian, "%H:%M %d/%m/%Y").replace(tzinfo=VN_TZ)
                diff_minutes = (datetime.datetime.now(VN_TZ) - match_time).total_seconds() / 60
                if -10 <= diff_minutes <= 120:
                    is_live, status = True, "Đang trực tiếp 🔴"
                elif diff_minutes > 120:
                    is_live, status = False, "Đã kết thúc 🏁"
                elif diff_minutes < -10:
                    is_live, status = False, "Chưa đá ⏳"
            except Exception:
                pass  # giữ nguyên is_live từ HTML

            matches_data.append({
                "id":         str(i + 1),
                "title":      f"{doi_nha} vs {doi_khach}",
                "doi_nha":    doi_nha,
                "doi_khach":  doi_khach,
                "trang_thai": status,
                "is_live":    is_live,
                "thoi_gian":  thoi_gian,
                "logo_nha":   logo_nha,
                "logo_khach": logo_khach,
                "link_xem":   item["href"],
                "stream_url": WAITING_VIDEO_URL,
            })
            print(f"   [{i+1:2d}/{len(parsed_list)}] {'🔴' if is_live else '⚪'} "
                  f"{doi_nha} vs {doi_khach} | {thoi_gian}"
                  + (f" | Logo✓" if logo_nha else " | Logo✗"))

        # ── BƯỚC 3: Bắt luồng m3u8 ──────────────────────────────────
        live_matches = [m for m in matches_data if m["is_live"]]
        print(f"\n🎥 BƯỚC 3: Bắt luồng {len(live_matches)} trận live...")

        for idx, match in enumerate(live_matches):
            print(f"\n   [{idx+1}/{len(live_matches)}] {match['title']}")
            stream = capture_stream(ctx, match["link_xem"])
            if stream:
                match["stream_url"] = stream
                print(f"   📡 {stream[:90]}")
            else:
                print("   ❌ Không tìm được luồng")

        browser.close()

    if not matches_data:
        print("\n❌ Không có dữ liệu!")
        return

    live_cnt   = sum(1 for m in matches_data if m["is_live"])
    stream_cnt = sum(1 for m in matches_data if m["stream_url"] != WAITING_VIDEO_URL)
    push_to_github(create_json(matches_data), live_cnt, stream_cnt)
    print(f"\n{'='*65}")
    print(f"✅ XONG — {len(matches_data)} trận | {live_cnt} live | {stream_cnt} có luồng")
    print("=" * 65)


if __name__ == "__main__":
    scrape_and_push()
