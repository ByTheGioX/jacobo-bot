"""
Script de prueba SOLO para los módulos de fotos.
Abre WhatsApp Web, scrapea el grupo de fotos, hace match con Turitop,
envía fotos a clientes, y chequea respuestas pendientes.

Uso: python test_photo_modules.py
"""
import datetime
import json
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
import re
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
import os
import sys
from webdriver_manager.chrome import ChromeDriverManager
import base64
import urllib.request
import pyautogui
import pyperclip
import subprocess
import hashlib


# ============================================================
# CONFIG
# ============================================================
# --- Read Photo Group Config ---
photo_group_config_fp = os.path.join(sys.path[0], 'data', 'photo_group_config.txt')
if not os.path.exists(photo_group_config_fp):
    os.makedirs(os.path.dirname(photo_group_config_fp), exist_ok=True)
    with open(photo_group_config_fp, 'w', encoding='utf-8') as f:
        f.write("Parapark Fotos 2026\nhttps://chat.whatsapp.com/J9kambMiqGYGg4GULS4LiM?mode=gi_t")

with open(photo_group_config_fp, 'r', encoding='utf-8') as f:
    config_lines = [L.strip() for L in f.readlines() if L.strip()]
    photo_group_name = config_lines[0] if len(config_lines) > 0 else 'Parapark Fotos 2026'
    photo_group_link = config_lines[1] if len(config_lines) > 1 else 'https://chat.whatsapp.com/J9kambMiqGYGg4GULS4LiM?mode=gi_t'

openrouter_api_key_fp = os.path.join(sys.path[0], 'data', 'openrouter_api_key.txt')
if not os.path.exists(openrouter_api_key_fp):
    with open(openrouter_api_key_fp, 'w', encoding='utf-8') as f:
        f.write('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')
with open(openrouter_api_key_fp, 'r', encoding='utf-8') as f:
    openrouter_api_key = f.read().strip()

google_maps_review_link = 'https://g.page/r/CRIIJJreA48cEAo/review'

pending_replies_fp = os.path.join(sys.path[0], 'data', 'pending_replies.json')
last_group_message_fp = os.path.join(sys.path[0], 'data', 'last_group_message.txt')
downloaded_photos_dir = os.path.join(sys.path[0], 'data', 'downloaded_photos')
photo_sent_messages_fp = os.path.join(sys.path[0], 'data', 'photo_sent_messages.txt')
log_fp = os.path.join(sys.path[0], 'data', 'logs.txt')
log_dir = os.path.join(sys.path[0], 'data', 'log')
photo_history_fp = os.path.join(sys.path[0], 'data', 'photo_history.json')

photo_thank_you_template_fp = os.path.join(sys.path[0], 'data', 'photo_thank_you_template.txt')
positive_review_template_fp = os.path.join(sys.path[0], 'data', 'positive_review_template.txt')
negative_review_template_fp = os.path.join(sys.path[0], 'data', 'negative_review_template.txt')
bad_caption_ids_fp = os.path.join(sys.path[0], 'data', 'bad_caption_replied_ids.json')
failed_sends_fp = os.path.join(sys.path[0], 'data', 'failed_photo_sends.json')
reminder_template_fp = os.path.join(sys.path[0], 'data', 'reminder_template.txt')
neutral_replies_fp = os.path.join(sys.path[0], 'data', 'neutral_replies.json')

if not os.path.exists(reminder_template_fp):
    with open(reminder_template_fp, 'w', encoding='utf-8') as f:
        f.write("¡Hola! 😊\n\nHace unos días os enviamos vuestra foto en PARAPARK MÁLAGA 📸 ¡Esperamos que lo hayáis pasado de miedo!\n\n¿Qué tal fue vuestra experiencia? ¡Nos encantaría saberlo! 🎉")

# Photo to send with positive review response (client can use .jpg, .jpeg, or .png)
review_photo_fp = None
for _ext in ['jpg', 'jpeg', 'png']:
    _candidate = os.path.join(sys.path[0], 'data', f'review_photo.{_ext}')
    if os.path.exists(_candidate):
        review_photo_fp = _candidate
        break
if not review_photo_fp:
    review_photo_fp = os.path.join(sys.path[0], 'data', 'review_photo.jpg')  # default for error msg

sala_to_places = {
    '4e': ['P1'],
    'csi': ['P2'],
    'maf': ['P4'],
    'tri': ['P5', 'P6'],
}

# Only process WhatsApp messages sent within this many hours.
# Fotos más antiguas que este límite se descartan definitivamente.
# 7 días permite recuperar fotos acumuladas si el bot estuvo offline varios días.
MAX_MESSAGE_AGE_HOURS = 168  # 7 días

os.system("title Test Photo Modules")


def parse_wa_pre_plain_text(pre_text):
    """Parse WhatsApp's data-pre-plain-text attribute into a datetime.

    Format examples:
      '[19:39, 17/4/2026] Juan:'
      '[7:05 p. m., 17/4/2026] Juan:'
      '[7:05 PM, 4/17/2026] Juan:'
    Returns a naive datetime, or None if parsing fails.
    """
    if not pre_text:
        return None
    m = re.search(
        r'\[(\d{1,2}):(\d{2})(?:\s*([ap])\.?\s*m\.?)?,\s*(\d{1,2})/(\d{1,2})/(\d{4})\]',
        pre_text,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2))
    ampm = m.group(3)
    a = int(m.group(4))
    b = int(m.group(5))
    year = int(m.group(6))
    if ampm:
        ap = ampm.lower()
        if ap == 'p' and hh < 12:
            hh += 12
        elif ap == 'a' and hh == 12:
            hh = 0
    # WhatsApp in Spanish uses d/m/yyyy; English often m/d/yyyy.
    # Heuristic: if first field > 12, it's the day (d/m). Otherwise
    # fall back to d/m (Spanish locale is our default here).
    if a > 12 and b <= 12:
        day, month = a, b
    elif b > 12 and a <= 12:
        day, month = b, a
    else:
        day, month = a, b  # default to d/m
    try:
        return datetime.datetime(year, month, day, hh, mm)
    except ValueError:
        return None


def message_within_window(pre_text, hours=MAX_MESSAGE_AGE_HOURS):
    """True if the WA message timestamp is within the last `hours`.

    If the timestamp cannot be parsed, returns False (safer: skip unknown).
    """
    dt = parse_wa_pre_plain_text(pre_text)
    if dt is None:
        return False
    age = datetime.datetime.now() - dt
    return datetime.timedelta(0) <= age <= datetime.timedelta(hours=hours)


# ============================================================
# HELPER: Cache for bad caption replies and failed sends
# ============================================================
def _load_json_set(filepath):
    """Load a JSON list from file, return as set."""
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return set(json.load(f))
        except:
            pass
    return set()


def _save_json_set(filepath, data_set):
    """Save a set as JSON list to file (keep last 500)."""
    items = list(data_set)[-500:]
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(items, f)


def write_execution_log(result, details):
    """Write a log entry to data/log/YYYY-MM-DD.log"""
    os.makedirs(log_dir, exist_ok=True)
    now = datetime.datetime.now()
    log_file = os.path.join(log_dir, now.strftime('%Y-%m-%d') + '.log')
    line = f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] {result} | {details}\n"
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(line)


def save_photo_history(entry):
    """Append a photo send record to photo_history.json for traceability."""
    history = []
    if os.path.exists(photo_history_fp):
        try:
            with open(photo_history_fp, 'r', encoding='utf-8') as f:
                history = json.load(f)
        except:
            history = []
    history.append(entry)
    # Keep last 1000 entries
    history = history[-1000:]
    with open(photo_history_fp, 'w', encoding='utf-8') as f:
        json.dump(history, f, indent=2, ensure_ascii=False)


def _load_failed_sends():
    """Load failed sends tracking dict."""
    if os.path.exists(failed_sends_fp):
        try:
            with open(failed_sends_fp, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except:
            pass
    return {}


def _save_failed_sends(data):
    with open(failed_sends_fp, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def track_failed_send(send_id, booking_info):
    """Increment failure counter for a send. Returns the new attempt count."""
    failed = _load_failed_sends()
    now_iso = datetime.datetime.now().isoformat()
    if send_id in failed:
        failed[send_id]['attempts'] = failed[send_id].get('attempts', 0) + 1
        failed[send_id]['last_failure'] = now_iso
    else:
        failed[send_id] = {
            'attempts': 1,
            'first_failure': now_iso,
            'last_failure': now_iso,
            'notified': False,
            'booking_info': booking_info,
        }
    _save_failed_sends(failed)
    return failed[send_id]['attempts']


def clear_failed_send(send_id):
    """Remove a send_id from failure tracking (called on success)."""
    failed = _load_failed_sends()
    if send_id in failed:
        del failed[send_id]
        _save_failed_sends(failed)


_session_actions = []  # Tracks actions for the current execution session

def log_action(booking_code, phone, status, response='-', review='-'):
    """Add an action to the current session log."""
    _session_actions.append({
        'time': datetime.datetime.now().strftime('%H:%M'),
        'booking': booking_code,
        'phone': phone,
        'status': status,
        'response': response,
        'review': review,
    })

def write_session_log():
    """Write the session actions to the hourly log file."""
    if not _session_actions:
        return
    os.makedirs(log_dir, exist_ok=True)
    now = datetime.datetime.now()
    log_file = os.path.join(log_dir, now.strftime('%Y-%m-%d') + '.log')

    start_time = _session_actions[0]['time']
    end_time = _session_actions[-1]['time']

    lines = []
    lines.append(f'\n=== SESIÓN {start_time}-{end_time} ({now.strftime("%d/%m/%Y")}) ===')
    lines.append(f'{"RESERVA":<25} {"TELÉFONO":<20} {"ESTADO":<12} {"RESPUESTA":<25} REVIEW')
    lines.append('-' * 95)
    for a in _session_actions:
        lines.append(
            f'{a["booking"]:<25} {a["phone"]:<20} {a["status"]:<12} {a["response"]:<25} {a["review"]}'
        )
    lines.append('')

    with open(log_file, 'a', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


# ============================================================
# BROWSER CLASS (copia simplificada)
# ============================================================
class Browser:
    def __init__(self):
        self.waiting_time = 60
        self.debug = True
        try:
            o = Options()
            desktop_path = os.path.join(os.environ['USERPROFILE'], 'Desktop')
            cache_path = os.path.join(desktop_path, 'browser_cache')
            o.add_argument(f'--user-data-dir={cache_path}')
            o.add_argument('--log-level=3')
            o.add_argument('--disable-session-crashed-bubble')
            o.add_experimental_option('excludeSwitches', ['enable-automation'])
            prefs = {"profile.exit_type": "Normal", "profile.exited_cleanly": True}
            o.add_experimental_option('prefs', prefs)

            for attempt in range(3):
                try:
                    self.driver_path = ChromeDriverManager().install()
                    break
                except Exception as e2:
                    self.driver_path = ''
                    print(f'driver download error: {e2}')
                    time.sleep(10)
            if self.driver_path == '':
                print("failed to get drivers from web.")
                sys.exit(1)

            self.driver_path = os.path.join(os.path.dirname(self.driver_path), 'chromedriver.exe')
            s = Service(executable_path=self.driver_path)
            self.web_browser = webdriver.Chrome(service=s, options=o)
            self.web_browser.set_window_size(width=1100, height=850)
        except Exception as e:
            print(str(e))
            sys.exit(1)

    def dismiss_restore_dialog(self):
        """Dismiss Chrome's 'Restore pages?' dialog if it appears."""
        try:
            # Try to dismiss via JavaScript - the dialog is a Chrome infobar
            self.web_browser.execute_script("""
                var buttons = document.querySelectorAll('button');
                buttons.forEach(function(btn) {
                    var text = btn.innerText.toLowerCase();
                    if (text.includes('restore') || text.includes('cancel') || text.includes('no') || text.includes('close')) {
                        btn.click();
                    }
                });
            """)
        except:
            pass
        try:
            # Also try keyboard shortcut to dismiss Chrome infobars
            from selenium.webdriver.common.action_chains import ActionChains
            ActionChains(self.web_browser).send_keys(Keys.ESCAPE).perform()
        except:
            pass

    def css_click(self, element):
        for count in range(0, self.waiting_time, 1):
            try:
                self.web_browser.find_element(By.CSS_SELECTOR, element).click()
                return True
            except:
                time.sleep(0.5)
        return False

    @staticmethod
    def re_get_text(re_pattern, raw_text):
        try:
            return re.findall(re_pattern, raw_text)[0].strip()
        except:
            return ''

    def get_text(self, element, parent_element=None):
        for count in range(0, 3, 1):
            try:
                if type(element) == str:
                    if parent_element is None:
                        t = self.web_browser.find_element(By.CSS_SELECTOR, element).get_attribute('innerText').strip()
                    else:
                        t = parent_element.find_element(By.CSS_SELECTOR, element).get_attribute('innerText').strip()
                else:
                    t = element.get_attribute('innerText').strip()
                return t
            except Exception as e:
                time.sleep(0.5)
        return ''

    def get_attr(self, element, attr, parent_element=None):
        for count in range(0, 3, 1):
            try:
                if type(element) == str:
                    if parent_element is None:
                        t = self.web_browser.find_element(By.CSS_SELECTOR, element).get_attribute(attr).strip()
                    else:
                        t = parent_element.find_element(By.CSS_SELECTOR, element).get_attribute(attr).strip()
                else:
                    t = element.get_attribute(attr).strip()
                return t
            except:
                time.sleep(0.5)
        return ''

    def x_click(self, element):
        for count in range(0, self.waiting_time, 1):
            try:
                self.web_browser.find_element(By.XPATH, element).click()
                return True
            except:
                time.sleep(0.5)
        return False

    def css_click_with_timer(self, element, timer):
        for count in range(0, timer, 1):
            try:
                self.web_browser.find_element(By.CSS_SELECTOR, element).click()
                return True
            except:
                try:
                    elem = self.web_browser.find_element(By.CSS_SELECTOR, element)
                    self.web_browser.execute_script('arguments[0].click()', elem)
                    return True
                except:
                    time.sleep(0)
                time.sleep(0.5)
        return False

    def x_click_with_timer(self, element, timer):
        for count in range(0, timer, 1):
            try:
                self.web_browser.find_element(By.XPATH, element).click()
                return True
            except:
                time.sleep(0.5)
        return False

    def js_click(self, element):
        for count in range(0, self.waiting_time, 1):
            try:
                ele = self.web_browser.find_element(By.CSS_SELECTOR, element)
                self.web_browser.execute_script('arguments[0].click()', ele)
                return True
            except:
                time.sleep(0.5)
        return False

    def elem_wait(self, element):
        for count in range(0, self.waiting_time, 1):
            try:
                ele = self.web_browser.find_elements(By.CSS_SELECTOR, element)
                if len(ele) > 0:
                    return True
                else:
                    time.sleep(0.5)
            except:
                time.sleep(0.5)
        return False

    def send_keys(self, element, keys, full=False, clear=False):
        for count in range(0, self.waiting_time, 1):
            try:
                ele = self.web_browser.find_element(By.CSS_SELECTOR, element)
                if full:
                    if clear:
                        ele.clear()
                    ele.send_keys(keys)
                else:
                    ele.clear()
                    for key_pack in keys.split("\n"):
                        ele.send_keys(key_pack)
                        ActionChains(self.web_browser).key_down(Keys.ALT).perform()
                        ActionChains(self.web_browser).send_keys(Keys.ENTER).perform()
                        ActionChains(self.web_browser).key_up(Keys.ALT).perform()
                return True
            except:
                time.sleep(0.5)
        return False

    def get(self, url):
        for try_ in range(0, 3, 1):
            try:
                self.web_browser.get(url)
                return True
            except Exception as e:
                print(f'get error: {e}')
                time.sleep(0.5)
        return False


# ============================================================
# MODULE 1 — Scrape Photo Group
# ============================================================

def _clean_wa_link(link):
    """Strip garbage chars (non-breaking spaces, word-joiners) from a WhatsApp send link.

    Turitop embeds phone numbers with U+00A0 / U+202C characters that cause
    WhatsApp Web to show a confirmation dialog instead of opening the chat directly.
    """
    try:
        import urllib.parse
        decoded = urllib.parse.unquote(link)
        if 'phone=' in decoded:
            base, phone_part = decoded.split('phone=', 1)
            phone_clean = re.sub(r'\D', '', phone_part)
            return base.replace('api.', 'web.') + 'phone=' + phone_clean
    except Exception:
        pass
    return link.replace("%20", "").lower().replace("api.", "web.")


def _wa_is_connected():
    """True si WhatsApp Web muestra la interfaz principal (no el QR)."""
    try:
        src = wb.web_browser.page_source
        if 'data-testid="qrcode"' in src or 'data-ref=' in src:
            return False
        if any(x in src for x in ['data-testid="chat-list"', 'aria-label="Lista de chats"',
                                   'side', '_pn_list_']):
            return True
    except Exception:
        pass
    return False


def _open_wa_chat(link):
    """Navega a un chat de WhatsApp, maneja el diálogo intermedio si aparece,
    y devuelve el elemento chat input (o None si falla o WhatsApp está desconectado)."""
    # Detectar desconexión antes de navegar
    if not _wa_is_connected():
        print('  [WARN] _open_wa_chat: WhatsApp desconectado (QR visible), saltando.')
        return None

    clean_link = _clean_wa_link(link)
    wb.get(clean_link)
    time.sleep(3)

    # Detectar desconexión después de navegar (el redirect al QR puede ocurrir aquí)
    if not _wa_is_connected():
        print('  [WARN] _open_wa_chat: WhatsApp se desconectó tras navegar, saltando.')
        return None

    # Clickear "Continuar al chat" / "Abrir chat" si WhatsApp muestra diálogo intermedio
    dialog_selectors = [
        "div[data-testid='popup-contents'] button",
        "div[data-testid='mi-landing-switch-app'] a",
        "a[data-testid='link-device-phone-number-confirmation-ok']",
        "//button[contains(text(),'Continuar')]",
        "//button[contains(text(),'Continue')]",
        "//a[contains(text(),'Continuar')]",
        "//a[contains(text(),'Abrir chat')]",
        "//div[contains(@class,'landing')]//button",
    ]
    for sel in dialog_selectors:
        try:
            if sel.startswith('//'):
                elem = wb.web_browser.find_element(By.XPATH, sel)
            else:
                elem = wb.web_browser.find_element(By.CSS_SELECTOR, sel)
            elem.click()
            time.sleep(2)
            break
        except Exception:
            continue

    # Esperar hasta 10s a que aparezca el input del chat
    chat_input_selectors = [
        "footer div[contenteditable='true']",
        "div[contenteditable='true'][data-tab='10']",
        "div[contenteditable='true'][data-tab='6']",
        "div[contenteditable='true'][data-tab]",
        "div[aria-placeholder='Escribe un mensaje']",
        "div[aria-placeholder='Type a message']",
    ]
    for _ in range(10):
        for sel in chat_input_selectors:
            try:
                elem = wb.web_browser.find_element(By.CSS_SELECTOR, sel)
                if elem:
                    return elem
            except Exception:
                continue
        time.sleep(1)

    print('  [WARN] _open_wa_chat: chat input no encontrado tras 10s.')
    return None


def parse_photo_caption(caption_text):
    caption_text = caption_text.lower().strip()
    # Use search instead of match to find the pattern anywhere in the text block
    # Format: dd/m HH:MM[/mm[/mm[/mm]]] sala[/sala2/sala3/sala4]
    # Examples:
    #   29/3 16:00 4e                     → 1 sala, 1 hora
    #   29/3 16:00/10 4e/csi              → 2 salas, 2 horas (16:00, 16:10)
    #   29/3 16:00/10/20 4e/maf/csi       → 3 salas, 3 horas (16:00, 16:10, 16:20)
    #   29/3 16:00/10/20/30 4e/maf/csi/tri → 4 salas, 4 horas (16:00, 16:10, 16:20, 16:30)
    match = re.search(
        r'(\d{1,2}/\d{1,2})\s+(\d{1,2}:\d{2}(?:/\d{2})*)\s+((?:4e|csi|maf|tri)(?:/(?:4e|csi|maf|tri))*)',
        caption_text
    )
    if not match:
        return None

    date_str = match.group(1)
    time_part = match.group(2)   # e.g. "16:00/10/20/30"
    salas = match.group(3).split('/')

    # Parse times: first is full HH:MM, rest are just minutes with same hour
    time_parts = time_part.split('/')
    time1 = time_parts[0]        # e.g. "16:00"
    hour = time1.split(':')[0]   # e.g. "16"
    times = [time1]
    for extra_min in time_parts[1:]:
        times.append(f"{hour}:{extra_min}")

    return {'date': date_str, 'times': times, 'salas': salas}


def download_wa_image(img_element, save_path, max_retries=3):
    """Download an image from WhatsApp Web blob URL using JS fetch + base64.
    Retries up to max_retries times on failure."""
    for attempt in range(1, max_retries + 1):
        try:
            # Check that the element still has a valid blob src
            src = img_element.get_attribute('src') or ''
            if not src.startswith('blob:'):
                print(f'    attempt {attempt}: img src is not a blob ({src[:50]}), skipping')
                time.sleep(2)
                continue

            base64_data = wb.web_browser.execute_async_script("""
                var callback = arguments[arguments.length - 1];
                var imgEl = arguments[0];
                fetch(imgEl.src)
                    .then(function(r) { return r.blob(); })
                    .then(function(blob) {
                        var reader = new FileReader();
                        reader.onloadend = function() { callback(reader.result); };
                        reader.readAsDataURL(blob);
                    })
                    .catch(function(err) { callback('ERROR:' + err.toString()); });
            """, img_element)

            if base64_data and not str(base64_data).startswith('ERROR:'):
                if ',' in base64_data:
                    base64_data = base64_data.split(',')[1]
                raw = base64.b64decode(base64_data)
                # Verify we got actual image data (at least 5KB to avoid thumbnails)
                if len(raw) < 5000:
                    print(f'    attempt {attempt}: downloaded data too small ({len(raw)} bytes), might be thumbnail. Retrying...')
                    time.sleep(3)
                    continue
                with open(save_path, 'wb') as f:
                    f.write(raw)
                print(f'  [OK] photo downloaded ({len(raw)} bytes): {save_path}')
                return True
            else:
                print(f'    attempt {attempt}: [FAIL] download error: {base64_data}')
                time.sleep(3)
        except Exception as e:
            print(f'    attempt {attempt}: [FAIL] exception: {e}')
            time.sleep(3)

    print(f'  [FAIL] all {max_retries} download attempts failed for {save_path}')
    return False


def _delete_single_group_message(msg_id):
    """Delete one message from the currently-open group chat by its data-id.
    Called immediately after a photo is downloaded, while the DOM still has it."""
    try:
        msg_el = wb.web_browser.find_element(By.CSS_SELECTOR, f"div[data-id='{msg_id}']")
    except Exception:
        print(f'  [DELETE] Message {msg_id[:40]} not found in DOM, skipping delete.')
        return False

    try:
        wb.web_browser.execute_script("arguments[0].scrollIntoView({block:'center'});", msg_el)
        time.sleep(1)

        # Hover to reveal dropdown button
        wb.web_browser.execute_script("""
            arguments[0].dispatchEvent(new MouseEvent('mouseover',{bubbles:true}));
            arguments[0].dispatchEvent(new MouseEvent('mouseenter',{bubbles:true}));
        """, msg_el)
        ActionChains(wb.web_browser).move_to_element(msg_el).perform()
        time.sleep(1)

        # Click the dropdown button inside the message
        menu_opened = False
        for btn_sel in [
            "div[data-testid='msg-options-trigger']",
            "span[data-icon='down-context']",
            "span[data-icon='triangle-down']",
            "[aria-label='Más opciones']",
            "[aria-label='More options']",
        ]:
            try:
                btns = msg_el.find_elements(By.CSS_SELECTOR, btn_sel)
                if btns:
                    wb.web_browser.execute_script("arguments[0].click()", btns[0])
                    menu_opened = True
                    break
            except Exception:
                pass

        if not menu_opened:
            ActionChains(wb.web_browser).context_click(msg_el).perform()

        time.sleep(1)

        # Click "Eliminar" in the context menu
        eliminar_clicked = False
        for method in [
            lambda: wb.web_browser.find_element(By.CSS_SELECTOR, "[role='menuitem'][aria-label='Eliminar']").click(),
            lambda: wb.web_browser.find_element(By.XPATH, "//*[@role='menuitem' and contains(.,'Eliminar')]").click(),
        ]:
            try:
                method()
                eliminar_clicked = True
                break
            except Exception:
                pass

        if not eliminar_clicked:
            try:
                items = wb.web_browser.find_elements(By.CSS_SELECTOR, "[role='menuitem']")
                for it in items:
                    if 'eliminar' in (it.get_attribute('innerText') or '').lower() and 'para' not in (it.get_attribute('innerText') or '').lower():
                        it.click()
                        eliminar_clicked = True
                        break
            except Exception:
                pass

        if not eliminar_clicked:
            ActionChains(wb.web_browser).send_keys(Keys.ESCAPE).perform()
            print(f'  [DELETE] Could not open Eliminar menu for {msg_id[:40]}')
            return False

        time.sleep(2)

        # Click trash icon in bottom toolbar
        trash_clicked = False
        try:
            trash_btn = wb.web_browser.find_element(By.CSS_SELECTOR, "button[aria-label='Eliminar']")
            trash_btn.click()
            trash_clicked = True
        except Exception:
            pass
        if not trash_clicked:
            try:
                trash_btn = wb.web_browser.find_element(By.XPATH, "//button[.//svg/title[text()='ic-delete']]")
                trash_btn.click()
                trash_clicked = True
            except Exception:
                pass

        if not trash_clicked:
            ActionChains(wb.web_browser).send_keys(Keys.ESCAPE).perform()
            print(f'  [DELETE] Could not find trash button for {msg_id[:40]}')
            return False

        time.sleep(2)

        # Confirm "Eliminar para mí"
        confirmed = False
        try:
            buttons = wb.web_browser.find_elements(By.TAG_NAME, "button")
            for btn in buttons:
                txt = (btn.get_attribute('innerText') or '').strip().lower()
                if 'eliminar para mí' in txt or 'delete for me' in txt:
                    btn.click()
                    confirmed = True
                    break
        except Exception:
            pass
        if not confirmed:
            try:
                wb.web_browser.find_element(By.XPATH, "//button[contains(.,'Eliminar para mí')]").click()
                confirmed = True
            except Exception:
                pass

        if confirmed:
            time.sleep(2)
            print(f'  [DELETE] [OK] Deleted from group: {msg_id[:40]}')
            return True
        else:
            ActionChains(wb.web_browser).send_keys(Keys.ESCAPE).perform()
            print(f'  [DELETE] Could not confirm "Eliminar para mí" for {msg_id[:40]}')
            return False

    except Exception as e:
        print(f'  [DELETE] Error: {e}')
        try:
            ActionChains(wb.web_browser).send_keys(Keys.ESCAPE).perform()
        except Exception:
            pass
        return False


def scrape_photo_group():
    photo_entries = []
    try:
        print('--- Module 1: Scraping photo group ---')

        # Navigate to group by searching its name in WhatsApp
        print(f'  Searching for group: "{photo_group_name}"')

        # Click search box
        search_clicked = wb.css_click_with_timer(
            "div[contenteditable='true'][data-tab='3']", 15
        )
        if not search_clicked:
            # Try alternative search selectors
            search_clicked = wb.css_click_with_timer(
                "div[role='textbox'][data-tab='3']", 10
            )
        if not search_clicked:
            search_clicked = wb.x_click_with_timer(
                "//div[@data-tab='3']", 10
            )
        print(f'  Search box clicked: {search_clicked}')
        time.sleep(2)

        # Type group name - Try multiple selectors for robustness
        search_box = None
        selectors_to_try = [
            ("div[contenteditable='true'][data-tab='3']", By.CSS_SELECTOR),
            ("div[role='textbox'][data-tab='3']", By.CSS_SELECTOR),
            ("//input[@type='text']", By.XPATH),
            ("input[type='text']", By.CSS_SELECTOR),
        ]

        for selector, by_type in selectors_to_try:
            try:
                search_box = wb.web_browser.find_element(by_type, selector)
                if search_box:
                    break
            except:
                continue

        if not search_box:
            print('  [ERROR] Could not find search box element!')
            return photo_entries

        try:
            search_box.clear()
        except:
            pass
        search_box.send_keys(photo_group_name)
        time.sleep(3)

        # Click on the group result
        group_clicked = wb.x_click_with_timer(
            f"//span[@title='{photo_group_name}']", 15
        )
        print(f'  Group clicked: {group_clicked}')
        if not group_clicked:
            print('  [ERROR] Could not find group in search results!')
            return photo_entries

        print('  Waiting for chat to load...')
        time.sleep(10)

        # Scroll up to load more messages (load enough history to catch all photos)
        try:
            chat_pane = None
            for pane_sel in ["div[role='application']", "div.copyable-area div[tabindex]", "div._ajyl"]:
                try:
                    chat_pane = wb.web_browser.find_element(By.CSS_SELECTOR, pane_sel)
                    break
                except:
                    continue
            if chat_pane:
                for _ in range(8):
                    wb.web_browser.execute_script("arguments[0].scrollTop = 0;", chat_pane)
                    time.sleep(2)
                # Scroll back down to load all messages in view
                wb.web_browser.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", chat_pane)
                time.sleep(3)
        except:
            print('  (could not scroll up, continuing with visible messages)')

        os.makedirs(downloaded_photos_dir, exist_ok=True)

        # --- DEBUG: Try multiple selectors to find messages ---
        selectors_to_try = [
            ("div.message-in", "Standard message-in"),
            ("div[class*='message-in']", "Partial class message-in"),
            ("div[data-id]", "Messages by data-id"),
            ("div.copyable-text", "Copyable text divs"),
            ("div[data-pre-plain-text]", "Pre-plain-text divs"),
            ("div[class*='_amjw']", "WhatsApp internal class"),
        ]

        print('\n  --- DEBUG: Testing CSS selectors ---')
        for selector, desc in selectors_to_try:
            try:
                found = wb.web_browser.find_elements(By.CSS_SELECTOR, selector)
                print(f'    {desc} ({selector}): {len(found)} elements')
            except Exception as e_sel:
                print(f'    {desc} ({selector}): ERROR - {e_sel}')

        # Also dump a sample of the actual HTML to understand structure
        print('\n  --- DEBUG: Looking for messages with images ---')
        all_imgs = wb.web_browser.find_elements(By.CSS_SELECTOR, "img[src*='blob:']")
        print(f'    Total blob images on page: {len(all_imgs)}')

        # Try to find message containers by looking for data-id attribute
        msg_containers = wb.web_browser.find_elements(By.CSS_SELECTOR, "div[data-id]")
        print(f'    Message containers (div[data-id]): {len(msg_containers)}')

        # Use div[data-id] as the primary selector since it's more stable
        messages = msg_containers
        if not messages:
            # Fallback: try message-in
            messages = wb.web_browser.find_elements(By.CSS_SELECTOR, "div.message-in")

        # Get all messages - we'll process all including our own photos
        # Deduplication is handled by processed_ids.json (msg_id tracking)
        incoming_messages = []
        for m in messages:
            try:
                incoming_messages.append(m)
            except Exception as e_filter:
                # If there's an error adding message, skip it
                continue

        print(f'\n  Found {len(incoming_messages)} incoming messages.')

        if len(incoming_messages) == 0:
            # Extra debug: print page title and URL
            print(f'  Current URL: {wb.web_browser.current_url}')
            print(f'  Page title: {wb.web_browser.title}')
            # Print a snippet of body text
            try:
                body_text = wb.web_browser.find_element(By.TAG_NAME, 'body').text[:500]
                print(f'  Body text preview: {body_text[:200]}...')
            except:
                print('  (could not retrieve body text preview)')

        # Use a list of processed IDs instead of a broken string timestamp comparison
        processed_ids_fp = os.path.join(sys.path[0], 'data', 'processed_photo_ids.json')
        reprocess = '--reprocess' in sys.argv
        processed_ids = []
        if reprocess:
            print('  [--reprocess] Ignoring processed IDs, will re-scan all messages.')
        elif os.path.exists(processed_ids_fp):
            try:
                with open(processed_ids_fp, 'r', encoding='utf-8') as f:
                    processed_ids = json.load(f)
            except:
                pass

        new_processed = set()

        # Collect bad caption message IDs to reply AFTER processing all good photos
        # (replying in the group refreshes the DOM and makes other elements stale)
        bad_caption_msg_ids = []

        for idx, msg in enumerate(incoming_messages):
            try:
                # 0. Get unique message ID
                msg_id = msg.get_attribute('data-id') or f"unknown_{idx}"
                if msg_id in processed_ids or msg_id in new_processed:
                    continue

                # Get timestamp for filename only
                copyable_els = msg.find_elements(By.CSS_SELECTOR, "[data-pre-plain-text]")
                if not copyable_els:
                    try:
                        parent = msg.find_element(By.XPATH, "./..")
                        copyable_els = parent.find_elements(By.CSS_SELECTOR, "[data-pre-plain-text]")
                    except:
                        pass

                if copyable_els:
                    pre_text = copyable_els[0].get_attribute("data-pre-plain-text").strip()
                    msg_timestamp = pre_text
                else:
                    pre_text = ''
                    msg_timestamp = f"msg_{idx}"

                # --- Filtro de antigüedad ---
                # Procesa fotos de los últimos MAX_MESSAGE_AGE_HOURS (7 días).
                # Si el bot estuvo offline varios días, al volver envía todo lo pendiente.
                # Solo se marcan como procesadas definitivamente las que superan los 7 días.
                no_age_filter = '--no-age-filter' in sys.argv
                if pre_text and not no_age_filter:
                    msg_dt = parse_wa_pre_plain_text(pre_text)
                    if msg_dt is not None:
                        age = datetime.datetime.now() - msg_dt
                        if age > datetime.timedelta(hours=MAX_MESSAGE_AGE_HOURS):
                            # Supera el límite máximo → marcar como procesada definitivamente
                            new_processed.add(msg_id)
                            continue
                        if age < datetime.timedelta(hours=-24):
                            # Más de 24h en el futuro → saltar sin marcar (dato inválido)
                            continue
                        # Si age es ligeramente negativa (≤ 24h), el reloj del VPS puede estar
                        # atrasado respecto al teléfono → procesar igualmente

                msg_text = msg.get_attribute('innerText') or msg.text or ''

                # 1. Parse caption FIRST. This acts as our primary filter.
                parsed = parse_photo_caption(msg_text)
                if parsed:
                    # Add msg_id and original caption for feedback tracking
                    parsed['msg_id'] = msg_id
                    parsed['caption'] = msg_text.strip()  # Store original caption
                if not parsed:
                    # Check if this message has a photo (image with bad/missing caption)
                    has_image = bool(msg.find_elements(By.CSS_SELECTOR, "img[src*='blob:']"))
                    if not has_image:
                        has_image = bool(msg.find_elements(By.CSS_SELECTOR, "span[data-icon='download'], span[data-icon='arrow-down']"))

                    if has_image:
                        # Defer reply to after all good photos are processed
                        bad_caption_replied = _load_json_set(bad_caption_ids_fp)
                        if msg_id not in bad_caption_replied:
                            bad_caption_msg_ids.append(msg_id)
                            print(f'\n  [MSG {idx}] Photo with BAD nomenclature detected (will reply later).')

                    new_processed.add(msg_id)
                    continue

                print(f'\n  [MSG {idx}] Found matching target: {parsed["date"]} {parsed["times"]} {"/".join(parsed["salas"])}')

                # 2. Check for image - try multiple strategies to find it
                img_elements = msg.find_elements(By.CSS_SELECTOR, "img[src*='blob:']")

                if not img_elements:
                    # Strategy A: Look for download button (image not auto-downloaded)
                    dl_icons = msg.find_elements(By.CSS_SELECTOR,
                        "span[data-icon='download'], span[data-icon='arrow-down'], span[data-icon='media-download']")
                    if dl_icons:
                        print('  -> Found download button. Clicking to download media...')
                        for dl_icon in dl_icons:
                            try:
                                wb.web_browser.execute_script("arguments[0].scrollIntoView(true);", dl_icon)
                                time.sleep(0.5)
                                try:
                                    dl_icon.find_element(By.XPATH, "..").click()
                                except:
                                    dl_icon.click()
                                break
                            except:
                                continue
                        # Wait longer for download to complete (images can be large)
                        for wait_round in range(15):
                            time.sleep(2)
                            # RE-FETCH the message element since DOM may have changed
                            try:
                                msg = wb.web_browser.find_element(By.CSS_SELECTOR, f"div[data-id='{msg_id}']")
                            except:
                                pass
                            img_elements = msg.find_elements(By.CSS_SELECTOR, "img[src*='blob:']")
                            if img_elements:
                                print(f'  -> Image appeared after {(wait_round+1)*2}s wait')
                                break
                    else:
                        # Strategy B: Click the message itself to trigger load
                        print('  -> No download icon. Trying to click the message body...')
                        try:
                            msg.click()
                        except:
                            pass
                        time.sleep(8)
                        try:
                            msg = wb.web_browser.find_element(By.CSS_SELECTOR, f"div[data-id='{msg_id}']")
                        except:
                            pass
                        img_elements = msg.find_elements(By.CSS_SELECTOR, "img[src*='blob:']")

                if not img_elements:
                    print('  -> [WARN] Could not find image blob after all strategies, will retry next cycle')
                    continue

                # Unique filename: use index + hash of msg_id to avoid collisions
                timestamp_clean = re.sub(r'[^\w]', '_', msg_timestamp)[:30]
                id_hash = hashlib.md5(msg_id.encode()).hexdigest()[:8]
                photo_filename = f"photo_{timestamp_clean}_{id_hash}.jpg"
                photo_path = os.path.join(downloaded_photos_dir, photo_filename)

                download_success = False

                # Download directly from this message's blob (no viewer needed).
                # Each message element has its own blob URL with its specific image.
                try:
                    msg = wb.web_browser.find_element(By.CSS_SELECTOR, f"div[data-id='{msg_id}']")
                    msg_imgs = msg.find_elements(By.CSS_SELECTOR, "img[src*='blob:']")
                except:
                    msg_imgs = img_elements

                if msg_imgs:
                    # Log the blob src to verify each message has a different one
                    try:
                        blob_src = msg_imgs[0].get_attribute('src')
                        print(f'  -> Downloading from message blob: {blob_src[:60]}...')
                    except:
                        print(f'  -> Downloading from message blob...')
                    for img_el in msg_imgs:
                        download_success = download_wa_image(img_el, photo_path)
                        if download_success:
                            break

                if download_success:
                    parsed['photo_path'] = photo_path
                    parsed['msg_timestamp'] = msg_timestamp
                    photo_entries.append(parsed)
                    print(f'  -> photo entry downloaded successfully!')
                    new_processed.add(msg_id)
                    # NOTE: delete is handled in Module 6 AFTER confirmed send,
                    # so the photo stays in the group as a safety net if send fails.
                else:
                    print(f'  -> [WARN] ALL download strategies failed. Will retry next cycle.')

            except Exception as e_msg:
                print(f'error processing message: {e_msg}')
                try:
                    wb.css_click_with_timer(
                        "span[data-icon='x'], span[data-icon='x-viewer']", 5
                    )
                except:
                    pass
                continue

        # Now handle deferred bad caption replies (after all good photos are processed)
        if bad_caption_msg_ids:
            print(f'\n--- Processing {len(bad_caption_msg_ids)} deferred bad caption replies ---')
            # Refresh messages to get current state
            time.sleep(2)
            incoming_messages = wb.web_browser.find_elements(By.CSS_SELECTOR, "div[data-id]")

            for msg_id in bad_caption_msg_ids:
                try:
                    # Find the message by data-id
                    msg_element = None
                    for msg in incoming_messages:
                        try:
                            if msg.get_attribute('data-id') == msg_id:
                                msg_element = msg
                                break
                        except:
                            pass

                    if not msg_element:
                        print(f'  [WARN] Message {msg_id} not found in current list, skipping reply.')
                        continue

                    print(f'  -> Replying to bad caption message: {msg_id}')
                    try:
                        # Long-press / right-click the message to reply
                        ActionChains(wb.web_browser).context_click(msg_element).perform()
                        time.sleep(1)
                        # Click "Responder" / "Reply" option
                        reply_clicked = wb.css_click_with_timer(
                            "div[aria-label='Responder'], div[aria-label='Reply'], li[data-animate-dropdown-item='true']:first-child",
                            5
                        )
                        if reply_clicked:
                            time.sleep(1)
                            reply_text = (
                                "⚠️ Esta foto no tiene el formato correcto.\n"
                                "Por favor reenvíala con el formato:\n\n"
                                "📝 *dia/mes hora sala*\n"
                                "Ejemplo: 24/3 19:10 csi\n"
                                "Varias salas: 24/3 19:10 csi/tri\n"
                                "Salas válidas: 4e, csi, maf, tri"
                            )
                            pyperclip.copy(reply_text)
                            time.sleep(0.5)
                            # Find the chat input and paste
                            chat_input = wb.web_browser.find_element(
                                By.CSS_SELECTOR, "footer div[contenteditable='true']"
                            )
                            wb.web_browser.execute_script("arguments[0].focus(); arguments[0].click();", chat_input)
                            time.sleep(0.5)
                            ActionChains(wb.web_browser).key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform()
                            time.sleep(1)
                            ActionChains(wb.web_browser).send_keys(Keys.ENTER).perform()
                            time.sleep(2)
                            print(f'  -> Replied with format instructions.')
                        else:
                            print(f'  -> Could not open reply menu.')
                            ActionChains(wb.web_browser).send_keys(Keys.ESCAPE).perform()
                            time.sleep(0.5)
                    except Exception as reply_err:
                        print(f'  -> Error replying to bad caption: {reply_err}')
                        try:
                            ActionChains(wb.web_browser).send_keys(Keys.ESCAPE).perform()
                        except:
                            pass

                    # Track that we already replied to this message
                    bad_caption_replied = _load_json_set(bad_caption_ids_fp)
                    bad_caption_replied.add(msg_id)
                    _save_json_set(bad_caption_ids_fp, bad_caption_replied)

                except Exception as e_bad_caption:
                    print(f'  [ERROR] processing bad caption reply: {e_bad_caption}')

        # Save updated processed IDs
        if new_processed:
            processed_ids.extend(list(new_processed))
            # Keep only the last 500 to avoid massive files
            processed_ids = processed_ids[-500:]
            with open(processed_ids_fp, 'w', encoding='utf-8') as f:
                json.dump(processed_ids, f)
            print('  -> updated processed message IDs.')

        if len(photo_entries) == 0:
            print('  No new messages found.')
        print(f'\n========== MODULE 1 DONE: {len(photo_entries)} photo entries ==========')

    except Exception as e:
        print(f'  [ERROR] scrape_photo_group: {e}, line: {e.__traceback__.tb_lineno}')

    return photo_entries


# ============================================================
# MODULE 2 — Match with Turitop & Send Photo to Client
# ============================================================

def scrape_turitop_for_date(target_day, target_month):
    bookings = []
    try:
        year = datetime.datetime.now().year
        target_date = f"{int(target_day):02d}-{int(target_month):02d}-{year}"
        print(f'  Scraping Turitop for date: {target_date}')

        wb.web_browser.execute_script("window.open('')")
        wb.web_browser.switch_to.window(wb.web_browser.window_handles[-1])

        wb.get("https://app.turitop.com/admin/company/P271/bookings")
        time.sleep(5)

        if "admin/login/es/" in wb.web_browser.current_url:
            print("  [WARN] Turitop logged out! Skipping.")
            wb.web_browser.close()
            wb.web_browser.switch_to.window(wb.web_browser.window_handles[0])
            return bookings

        wb.send_keys("#filter_event_date_from", target_date, full=True, clear=True)
        wb.send_keys("#filter_event_date_to", target_date, full=True, clear=True)
        time.sleep(2)
        wb.js_click("button[type=submit][name=action]")
        time.sleep(3)

        for booking_page in range(1, 5):
            wb.send_keys("#filter_event_date_from", target_date, full=True, clear=True)
            wb.send_keys("#filter_event_date_to", target_date, full=True, clear=True)
            time.sleep(1)
            wb.js_click("button[type=submit][name=action]")
            time.sleep(3)
            wb.get(f"https://app.turitop.com/admin/company/P271/bookings/list/page/{booking_page}")
            time.sleep(5)

            rows = wb.web_browser.find_elements(
                By.CSS_SELECTOR, "tr.bookings-history-row:not([class*='deleted'])"
            )
            if len(rows) == 0:
                break

            for each in rows:
                try:
                    booking_day = wb.get_text("div.format-day-of-month-number", each)
                    booking_time = wb.get_text("div.format-time-short", each)
                    booking_place = wb.get_text("span.bookings-product-name", each)
                    wa_link = wb.get_attr("a.whatsapp-button", "href", each)
                    booking_month = wb.get_text("div.format-month-name-short", each)
                    booking_year = wb.get_text("div.format-year-long", each)
                    booking_date = f'{booking_day} {booking_month} {booking_year}'

                    d = {
                        "booking_time": booking_time,
                        "booking_day": booking_day,
                        "booking_place": booking_place,
                        "wa_link": wa_link,
                        "booking_date": booking_date,
                    }
                    if d not in bookings:
                        bookings.append(d)
                except Exception as e_inner:
                    print(f'  [ERROR] row: {e_inner}')

        print(f'  Found {len(bookings)} bookings for {target_date}')

        wb.web_browser.close()
        wb.web_browser.switch_to.window(wb.web_browser.window_handles[0])

    except Exception as e:
        print(f'  [ERROR] scrape_turitop: {e}, line: {e.__traceback__.tb_lineno}')
        try:
            if len(wb.web_browser.window_handles) > 1:
                wb.web_browser.close()
                wb.web_browser.switch_to.window(wb.web_browser.window_handles[0])
        except:
            pass

    return bookings


def delete_sent_photos_from_group(msg_ids):
    """Navigate to photo group and delete all successfully sent messages ('Eliminar para mí').
    Opens the group once and deletes all messages from there.

    Uses the real WhatsApp Web HTML structure:
    - Context menu items: div[role='menuitem'][aria-label='Eliminar']
    - Confirmation dialog buttons: <button> with text 'Eliminar para mí'
    """
    deleted_count = 0
    try:
        print(f'\n========== MODULE 6: Deleting {len(msg_ids)} sent photos from group ==========')

        if not msg_ids:
            print('  No photos to delete.')
            print('========== MODULE 6 DONE ==========')
            return 0

        # Make sure we're on the WhatsApp tab (close any extra tabs first)
        while len(wb.web_browser.window_handles) > 1:
            wb.web_browser.switch_to.window(wb.web_browser.window_handles[-1])
            wb.web_browser.close()
        wb.web_browser.switch_to.window(wb.web_browser.window_handles[0])

        # Navigate to WhatsApp main page to reset state
        wb.get("https://web.whatsapp.com")
        print('  Waiting for WhatsApp to load...')
        time.sleep(15)

        # Press ESC to close any open dialogs/panels
        ActionChains(wb.web_browser).send_keys(Keys.ESCAPE).perform()
        time.sleep(1)
        ActionChains(wb.web_browser).send_keys(Keys.ESCAPE).perform()
        time.sleep(1)

        # Navigate to group
        print(f'  Searching for group: "{photo_group_name}"')

        # Click search box
        search_clicked = wb.css_click_with_timer(
            "div[contenteditable='true'][data-tab='3']", 15
        )
        if not search_clicked:
            search_clicked = wb.css_click_with_timer(
                "div[role='textbox'][data-tab='3']", 10
            )
        if not search_clicked:
            search_clicked = wb.x_click_with_timer(
                "//div[@data-tab='3']", 10
            )
        print(f'  Search box clicked: {search_clicked}')
        time.sleep(2)

        # Type group name in search box
        search_box = None
        selectors_to_try = [
            ("div[contenteditable='true'][data-tab='3']", By.CSS_SELECTOR),
            ("div[role='textbox'][data-tab='3']", By.CSS_SELECTOR),
            ("//input[@type='text']", By.XPATH),
            ("input[type='text']", By.CSS_SELECTOR),
        ]

        for selector, by_type in selectors_to_try:
            try:
                search_box = wb.web_browser.find_element(by_type, selector)
                if search_box:
                    break
            except:
                continue

        if search_box:
            try:
                search_box.clear()
            except:
                pass
            search_box.send_keys(photo_group_name)
            time.sleep(3)
            # Press Enter to open the first search result
            search_box.send_keys(Keys.ENTER)
            time.sleep(2)
        else:
            print('  Search box not found, trying to click group directly...')

        # Also try clicking the group directly as backup
        try:
            group_el = wb.web_browser.find_element(
                By.XPATH, f"//span[@title='{photo_group_name}']"
            )
            group_el.click()
        except:
            pass
        time.sleep(2)

        # Verify chat opened: check for message containers (div[data-id])
        group_opened = False
        for wait in range(10):
            try:
                msgs = wb.web_browser.find_elements(By.CSS_SELECTOR, "div[data-id]")
                if len(msgs) > 0:
                    group_opened = True
                    break
            except:
                pass
            time.sleep(1)

        print(f'  Group opened: {group_opened}')
        if not group_opened:
            print('  [ERROR] Could not open photo group chat.')
            ActionChains(wb.web_browser).send_keys(Keys.ESCAPE).perform()
            print('========== MODULE 6 DONE: 0 photos deleted ==========')
            return 0

        print('  Waiting for chat to load...')
        time.sleep(5)

        # Scroll up to load older messages so recently-sent photos (which may
        # be a few dozen messages up) are in the DOM and can be found by data-id.
        try:
            chat_pane = None
            for pane_sel in ["div[role='application']", "div.copyable-area div[tabindex]", "div._ajyl"]:
                try:
                    chat_pane = wb.web_browser.find_element(By.CSS_SELECTOR, pane_sel)
                    break
                except:
                    continue
            if chat_pane:
                for _ in range(4):
                    wb.web_browser.execute_script("arguments[0].scrollTop = 0;", chat_pane)
                    time.sleep(1.5)
                # Scroll back down to bottom so the UI is in a stable state
                wb.web_browser.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", chat_pane)
                time.sleep(2)
        except:
            pass

        # Now delete each message one by one (already inside the group)
        not_found_ids = []
        for msg_id in msg_ids:
            try:
                print(f'\n  -> Deleting message {msg_id[:40]}...')

                # Find the message by data-id
                msg_el = None
                try:
                    msg_el = wb.web_browser.find_element(By.CSS_SELECTOR, f"div[data-id='{msg_id}']")
                except:
                    # Try scrolling up a few more times in case message is still not loaded
                    try:
                        if chat_pane:
                            for _ in range(3):
                                wb.web_browser.execute_script("arguments[0].scrollTop = 0;", chat_pane)
                                time.sleep(1.5)
                                try:
                                    msg_el = wb.web_browser.find_element(By.CSS_SELECTOR, f"div[data-id='{msg_id}']")
                                    break
                                except:
                                    continue
                    except:
                        pass

                if msg_el is None:
                    print(f'     [WARN] Message not found in DOM. NOT counted as deleted.')
                    not_found_ids.append(msg_id)
                    continue

                # Scroll to the message so it's visible
                wb.web_browser.execute_script("arguments[0].scrollIntoView({block: 'center'});", msg_el)
                time.sleep(1.5)

                # === STEP 1: Hover to reveal WhatsApp's dropdown button, then click it ===
                # context_click triggers the BROWSER menu, not WhatsApp's custom menu.
                # The correct way is: hover → click the down-arrow button that appears.

                # Trigger hover via JS + ActionChains
                wb.web_browser.execute_script("""
                    arguments[0].dispatchEvent(new MouseEvent('mouseover', {bubbles:true}));
                    arguments[0].dispatchEvent(new MouseEvent('mouseenter', {bubbles:true}));
                """, msg_el)
                ActionChains(wb.web_browser).move_to_element(msg_el).perform()
                time.sleep(1.5)

                # Find and click the hover dropdown button inside the message
                menu_btn_clicked = False
                for btn_sel in [
                    "div[data-testid='msg-options-trigger']",
                    "span[data-icon='down-context']",
                    "span[data-icon='triangle-down']",
                    "[aria-label='Más opciones']",
                    "[aria-label='More options']",
                    "div[class*='_amkv']",
                    "button[class*='message-options']",
                ]:
                    try:
                        btns = msg_el.find_elements(By.CSS_SELECTOR, btn_sel)
                        if btns:
                            wb.web_browser.execute_script("arguments[0].click()", btns[0])
                            menu_btn_clicked = True
                            print(f'     Step 1: Clicked hover menu button via {btn_sel}')
                            break
                    except:
                        pass

                if not menu_btn_clicked:
                    # Fallback: try right-click
                    ActionChains(wb.web_browser).context_click(msg_el).perform()
                    print(f'     Step 1: Fallback right-click used')

                time.sleep(1.5)

                # === STEP 2: Click "Eliminar" in the context menu ===
                eliminar_clicked = False

                # Debug: print what menu items appeared
                try:
                    all_items = wb.web_browser.find_elements(By.CSS_SELECTOR, "[role='menuitem'], li[role='option'], [data-testid*='menu-item']")
                    if all_items:
                        texts = [i.get_attribute('innerText').strip() for i in all_items if i.get_attribute('innerText').strip()]
                        print(f'     DEBUG menu items found: {texts}')
                    else:
                        # Broader search
                        all_visible = wb.web_browser.find_elements(By.XPATH, "//*[contains(text(),'Eliminar') or contains(text(),'Delete')]")
                        if all_visible:
                            print(f'     DEBUG found Eliminar/Delete elements: {len(all_visible)}')
                        else:
                            print(f'     DEBUG: no menu elements found in DOM')
                except:
                    pass

                # Method 1: aria-label
                try:
                    eliminar_item = wb.web_browser.find_element(
                        By.CSS_SELECTOR, "[role='menuitem'][aria-label='Eliminar'], [aria-label='Eliminar mensaje']"
                    )
                    eliminar_item.click()
                    eliminar_clicked = True
                    print(f'     Step 2: Clicked "Eliminar" (aria-label).')
                except:
                    pass

                # Method 2: any menuitem containing "eliminar"
                if not eliminar_clicked:
                    try:
                        menu_items = wb.web_browser.find_elements(By.CSS_SELECTOR, "[role='menuitem']")
                        for item in menu_items:
                            item_text = (item.get_attribute('innerText') or '').strip().lower()
                            if 'eliminar' in item_text and 'para' not in item_text:
                                item.click()
                                eliminar_clicked = True
                                print(f'     Step 2: Clicked "Eliminar" (text match).')
                                break
                    except:
                        pass

                # Method 3: XPath text
                if not eliminar_clicked:
                    try:
                        eliminar_item = wb.web_browser.find_element(
                            By.XPATH, "//*[@role='menuitem' and contains(.,'Eliminar')]"
                        )
                        eliminar_item.click()
                        eliminar_clicked = True
                        print(f'     Step 2: Clicked "Eliminar" (XPath).')
                    except:
                        pass

                # Method 4: data-testid
                if not eliminar_clicked:
                    try:
                        eliminar_item = wb.web_browser.find_element(
                            By.CSS_SELECTOR, "[data-testid='mi-msg-delete'], [data-testid='msg-delete']"
                        )
                        eliminar_item.click()
                        eliminar_clicked = True
                        print(f'     Step 2: Clicked "Eliminar" (data-testid).')
                    except:
                        pass

                if not eliminar_clicked:
                    print(f'     [WARN] Could not find "Eliminar" in context menu. Skipping.')
                    ActionChains(wb.web_browser).send_keys(Keys.ESCAPE).perform()
                    time.sleep(1)
                    continue

                # === STEP 3: Click trash icon button in bottom toolbar ===
                # After clicking "Eliminar", message enters selection mode with bottom toolbar
                time.sleep(2)
                trash_clicked = False

                # Method 1: button with aria-label="Eliminar"
                try:
                    trash_btn = wb.web_browser.find_element(
                        By.CSS_SELECTOR, "button[aria-label='Eliminar']"
                    )
                    trash_btn.click()
                    trash_clicked = True
                    print(f'     Step 3: Clicked trash button.')
                except:
                    pass

                # Method 2: Find button containing SVG with title "ic-delete"
                if not trash_clicked:
                    try:
                        trash_btn = wb.web_browser.find_element(
                            By.XPATH, "//button[.//svg/title[text()='ic-delete']]"
                        )
                        trash_btn.click()
                        trash_clicked = True
                        print(f'     Step 3: Clicked trash button (SVG).')
                    except:
                        pass

                if not trash_clicked:
                    print(f'     [WARN] Could not find trash button. Skipping.')
                    try:
                        cancel_btn = wb.web_browser.find_element(
                            By.CSS_SELECTOR, "button[aria-label='Cancelar eliminación']"
                        )
                        cancel_btn.click()
                    except:
                        ActionChains(wb.web_browser).send_keys(Keys.ESCAPE).perform()
                    time.sleep(1)
                    continue

                # === STEP 4: Click "Eliminar para mí" in confirmation dialog ===
                time.sleep(3)
                confirm_clicked = False

                # Method 1: Find button by text
                try:
                    buttons = wb.web_browser.find_elements(By.TAG_NAME, "button")
                    for btn in buttons:
                        btn_text = (btn.get_attribute('innerText') or '').strip().lower()
                        if 'eliminar para mí' in btn_text or 'delete for me' in btn_text:
                            btn.click()
                            confirm_clicked = True
                            print(f'     Step 4: Clicked "Eliminar para mí".')
                            break
                except:
                    pass

                # Method 2: XPath
                if not confirm_clicked:
                    try:
                        confirm_btn = wb.web_browser.find_element(
                            By.XPATH, "//button[contains(., 'Eliminar para mí')]"
                        )
                        confirm_btn.click()
                        confirm_clicked = True
                        print(f'     Step 4: Clicked "Eliminar para mí" (XPath).')
                    except:
                        pass

                # Method 3: Inside modal popup
                if not confirm_clicked:
                    try:
                        modal = wb.web_browser.find_element(
                            By.CSS_SELECTOR, "div[data-animate-modal-popup='true']"
                        )
                        modal_buttons = modal.find_elements(By.TAG_NAME, "button")
                        for btn in modal_buttons:
                            btn_text = (btn.get_attribute('innerText') or '').strip().lower()
                            if 'para mí' in btn_text or 'for me' in btn_text:
                                btn.click()
                                confirm_clicked = True
                                print(f'     Step 4: Clicked "Eliminar para mí" (modal).')
                                break
                    except:
                        pass

                if not confirm_clicked:
                    print(f'     [WARN] Could not confirm "Eliminar para mí".')
                    ActionChains(wb.web_browser).send_keys(Keys.ESCAPE).perform()
                    time.sleep(1)
                    continue

                time.sleep(2)
                deleted_count += 1
                print(f'     [OK] Deleted!')

            except Exception as msg_err:
                print(f'     [ERROR] {msg_err}')
                try:
                    ActionChains(wb.web_browser).send_keys(Keys.ESCAPE).perform()
                except:
                    pass
                time.sleep(1)

        if not_found_ids:
            print(f'\n  [WARN] {len(not_found_ids)} message(s) could NOT be located in DOM:')
            for nid in not_found_ids:
                print(f'    - {nid[:60]}')
            print('  (They were NOT counted as deleted. Will retry next cycle.)')

        print(f'\n========== MODULE 6 DONE: {deleted_count}/{len(msg_ids)} photos deleted ==========')

    except Exception as e:
        print(f'  [ERROR] delete_sent_photos_from_group: {e}, line: {e.__traceback__.tb_lineno}')
        print(f'========== MODULE 6 DONE: {deleted_count}/{len(msg_ids)} photos deleted ==========')

    return deleted_count


def match_and_send_photos(photo_entries):
    try:
        print('\n========== MODULE 2: Matching photos with bookings ==========')

        sent_photo_msg_ids = []  # Buffer of msg_ids to delete after all modules finish

        if not os.path.exists(photo_sent_messages_fp):
            with open(photo_sent_messages_fp, 'w') as f:
                f.write('')

        dates_to_scrape = set()
        for entry in photo_entries:
            dates_to_scrape.add(entry['date'])

        bookings_by_date = {}
        for date_str in dates_to_scrape:
            parts = date_str.split('/')
            day, month = parts[0], parts[1]
            bookings_by_date[date_str] = scrape_turitop_for_date(day, month)
            time.sleep(3)

        wb.web_browser.switch_to.window(wb.web_browser.window_handles[0])

        if not os.path.exists(pending_replies_fp):
            json.dump([], open(pending_replies_fp, 'w'), indent=2)
        pending = json.load(open(pending_replies_fp, 'r'))

        for entry in photo_entries:
          try:
            date_str = entry['date']
            salas = entry['salas']
            photo_path = entry['photo_path']
            sala_label = '/'.join(salas)
            day_num = date_str.split('/')[0]
            bookings = bookings_by_date.get(date_str, [])

            # Track wa_links we've already sent THIS photo to, to avoid duplicates
            # (e.g., maf/csi 16:00/10 where both bookings belong to the same customer)
            sent_wa_links_this_photo = set()
            entry_all_sends_ok = True  # Track if all sends for this entry succeeded

            # Match each time to its corresponding sala.
            # Caption "16:00/10 maf/csi" means: 16:00→maf, 16:10→csi
            # If there's only 1 time but multiple salas, send to all matching bookings.
            # If times and salas are paired (same count), each time matches its sala.
            times = entry['times']

            for time_idx, t in enumerate(times):
                msg_id = entry.get('msg_id')  # Get msg_id for feedback tracking

                # Determine which places to search for this specific time
                if len(times) == len(salas) and len(times) > 1:
                    # Paired: time[i] corresponds to sala[i]
                    specific_sala = salas[time_idx]
                    time_target_places = sala_to_places.get(specific_sala, [])
                    print(f'\n  Matching: date={date_str} time={t} sala={specific_sala} (paired)')
                else:
                    # Single time or mismatch: use all salas
                    time_target_places = []
                    for s in salas:
                        time_target_places.extend(sala_to_places.get(s, []))
                    print(f'\n  Matching: date={date_str} time={t} salas={sala_label}')

                matched_booking = None
                for b in bookings:
                    b_place = b['booking_place'].upper().replace('#', '')
                    if (b['booking_time'].strip() == t.strip() and
                            b['booking_day'].strip() == day_num.strip() and
                            any(p.upper() in b_place for p in time_target_places)):
                        matched_booking = b
                        break

                # Fallback: if paired match failed, try the other salas in the caption
                # This handles cases where the caption has the salas in wrong order
                # e.g. caption "16:00/10 csi/maf" but Turitop has 16:00→maf, 16:10→csi
                if not matched_booking and len(times) == len(salas) and len(times) > 1:
                    fallback_places = []
                    for s in salas:
                        fallback_places.extend(sala_to_places.get(s, []))
                    # Remove the places we already tried
                    fallback_places = [p for p in fallback_places if p not in time_target_places]
                    if fallback_places:
                        for b in bookings:
                            b_place = b['booking_place'].upper().replace('#', '')
                            if (b['booking_time'].strip() == t.strip() and
                                    b['booking_day'].strip() == day_num.strip() and
                                    any(p.upper() in b_place for p in fallback_places)):
                                matched_booking = b
                                print(f'  -> [FALLBACK] Matched via swapped sala order for time={t}')
                                break

                if not matched_booking:
                    print(f'  -> No matching booking found.')
                    continue

                if not matched_booking.get('wa_link'):
                    print('  -> Booking found but no WhatsApp link.')
                    continue

                # Skip if we already sent this same photo to this wa_link
                wa_link_normalized = matched_booking['wa_link'].strip().lower()
                if wa_link_normalized in sent_wa_links_this_photo:
                    print(f'  -> Already sent this photo to {matched_booking["wa_link"]}, skipping duplicate.')
                    continue

                sent_id = f"{date_str}_{t}_{sala_label}_{matched_booking['wa_link']}"

                with open(photo_sent_messages_fp, 'r') as f:
                    if sent_id in f.read():
                        print('  -> Already sent, skipping.')
                        continue

                # Note: we no longer permanently skip failed sends.
                # Each cycle gets a fresh chance to send.

                print(f'  -> Sending photo to: {matched_booking["wa_link"]}')
                _chat_input_ready = _open_wa_chat(matched_booking['wa_link'])

                invalid_markers = [
                    "Enlace incorrecto",
                    "El número de teléfono compartido a través de la dirección URL es inválido",
                    "El número de teléfono compartido a través de la dirección URL no es válido",
                    "no está en WhatsApp"
                ]
                if any(m in wb.web_browser.page_source for m in invalid_markers):
                    print('  -> Invalid WhatsApp link.')
                    continue
                if not _chat_input_ready:
                    print('  -> Could not open chat (chat input not found after navigation).')
                    continue

                with open(photo_thank_you_template_fp, 'r', encoding='utf-8') as f:
                    photo_thank_you_msg = f.read().strip()

                send_success = False
                for send_attempt in range(1, 3):  # Try up to 2 times
                    try:
                        # 1. Copy image to Windows clipboard via PowerShell FIRST
                        print(f"  -> Send attempt {send_attempt}: Copying image to OS clipboard...")
                        photo_ps_path = os.path.abspath(photo_path).replace("\\", "/")
                        ps_script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$img = [System.Drawing.Image]::FromFile('{photo_ps_path}')
[System.Windows.Forms.Clipboard]::SetImage($img)
$img.Dispose()
"""
                        subprocess.run(["powershell", "-STA", "-command", ps_script], check=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)

                        # 2. Re-focus browser window (PowerShell stole focus)
                        wb.web_browser.switch_to.window(wb.web_browser.current_window_handle)
                        time.sleep(2)

                        # 3. Click chat input to ensure it's focused (use JS to avoid intercept)
                        chat_input = None
                        for selector in [
                            "footer div[contenteditable='true']",
                            "div[contenteditable='true'][data-tab]",
                            "div[title='Escribe un mensaje aquí']",
                            "div[title='Escribe un mensaje']",
                            "div[title='Type a message']",
                            "div[aria-placeholder='Escribe un mensaje']"
                        ]:
                            try:
                                chat_input = wb.web_browser.find_element(By.CSS_SELECTOR, selector)
                                wb.web_browser.execute_script("arguments[0].scrollIntoView(true);", chat_input)
                                time.sleep(0.5)
                                wb.web_browser.execute_script("arguments[0].focus(); arguments[0].click();", chat_input)
                                break
                            except:
                                continue

                        if not chat_input:
                            print("  [WARN] Could not find chat input, trying active element...")

                        time.sleep(1)

                        # 4. Paste image into WhatsApp chat
                        print("  -> Pasting image into WhatsApp...")
                        paste_ok = False
                        for paste_try in range(3):
                            try:
                                if chat_input:
                                    chat_input.send_keys(Keys.CONTROL, 'v')
                                else:
                                    wb.web_browser.switch_to.active_element.send_keys(Keys.CONTROL, 'v')
                                paste_ok = True
                                break
                            except Exception as paste_err:
                                print(f"    paste try {paste_try+1} failed: {paste_err}")
                                time.sleep(1)
                                try:
                                    ActionChains(wb.web_browser).key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform()
                                    paste_ok = True
                                    break
                                except:
                                    time.sleep(2)

                        if not paste_ok:
                            print("  -> All paste attempts failed, retrying send...")
                            continue

                    except Exception as paste_e:
                        print(f'  [ERROR] Failed to paste image (attempt {send_attempt}): {paste_e}')
                        try:
                            ActionChains(wb.web_browser).send_keys(Keys.ESCAPE).perform()
                            time.sleep(1)
                        except:
                            pass
                        continue

                    # --- Send photo WITHOUT caption, then send text separately ---
                    # Wait for image preview overlay
                    print("  -> Waiting for image preview overlay...")
                    preview_found = False
                    preview_indicators = [
                        "span[data-icon='pencil']",
                        "span[data-icon='crop']",
                        "span[data-icon='scissors']",
                        "span[data-icon='text']",
                        "span[data-icon='sticker']",
                        "span[data-icon='wds-ic-send-filled']",
                        "div[aria-placeholder*='Añade']",
                        "div[aria-placeholder*='Add a caption']",
                    ]
                    for wait_i in range(15):
                        time.sleep(2)
                        # Check duplicate 'Escribe un mensaje' inputs (2+ = preview open)
                        try:
                            escribe_inputs = wb.web_browser.find_elements(By.CSS_SELECTOR, "div[aria-placeholder*='Escribe un mensaje']")
                            if len(escribe_inputs) >= 2:
                                preview_found = True
                                print(f"  -> Preview detected via duplicate inputs ({len(escribe_inputs)}) after {(wait_i+1)*2}s")
                                break
                        except:
                            pass
                        for sel in preview_indicators:
                            try:
                                if wb.web_browser.find_elements(By.CSS_SELECTOR, sel):
                                    preview_found = True
                                    print(f"  -> Preview detected via '{sel}' after {(wait_i+1)*2}s")
                                    break
                            except:
                                continue
                        if preview_found:
                            break

                    if not preview_found:
                        print(f"  -> Image preview did not appear (attempt {send_attempt}), retrying...")
                        try:
                            ActionChains(wb.web_browser).send_keys(Keys.ESCAPE).perform()
                            time.sleep(2)
                        except:
                            pass
                        continue

                    # Step A: Send the photo (no caption) by clicking the send button in preview
                    print("  -> Sending photo without caption...")
                    photo_sent = False
                    for send_sel in ["span[data-icon='send']", "span[data-icon='wds-ic-send-filled']", "div[aria-label='Enviar']"]:
                        try:
                            send_btn = wb.web_browser.find_element(By.CSS_SELECTOR, send_sel)
                            wb.web_browser.execute_script("arguments[0].click();", send_btn)
                            photo_sent = True
                            print(f"  -> Photo send clicked via '{send_sel}'")
                            break
                        except:
                            continue

                    if not photo_sent:
                        print(f"  -> Could not click send button (attempt {send_attempt}), retrying...")
                        try:
                            ActionChains(wb.web_browser).send_keys(Keys.ESCAPE).perform()
                            time.sleep(2)
                        except:
                            pass
                        continue

                    # Wait for photo to actually send (preview closes, message appears)
                    time.sleep(5)

                    # Step B: Now send the thank-you text as a separate message
                    print("  -> Sending thank-you text as separate message...")
                    text_sent = False
                    # Find the chat input box
                    chat_input_2 = None
                    for selector in [
                        "footer div[contenteditable='true']",
                        "div[contenteditable='true'][data-tab]",
                        "div[title='Escribe un mensaje aquí']",
                        "div[title='Escribe un mensaje']",
                        "div[title='Type a message']",
                        "div[aria-placeholder='Escribe un mensaje']"
                    ]:
                        try:
                            chat_input_2 = wb.web_browser.find_element(By.CSS_SELECTOR, selector)
                            break
                        except:
                            continue

                    if chat_input_2:
                        pyperclip.copy(photo_thank_you_msg)
                        time.sleep(0.5)
                        wb.web_browser.execute_script("arguments[0].focus(); arguments[0].click();", chat_input_2)
                        time.sleep(0.5)
                        ActionChains(wb.web_browser).key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform()
                        time.sleep(2)
                        # Press Enter to send the text message
                        ActionChains(wb.web_browser).send_keys(Keys.ENTER).perform()
                        time.sleep(3)
                        text_sent = True
                        print("  -> Text message sent!")
                    else:
                        print("  -> [WARN] Could not find chat input for text message")

                    # Verify message was sent
                    for check_i in range(10):
                        out_msgs = wb.web_browser.find_elements(By.CSS_SELECTOR, 'div.message-out')
                        if out_msgs:
                            send_success = True
                            break
                        time.sleep(2)

                    if send_success:
                        print('  -> [OK] Photo + text sent successfully!')
                        time.sleep(3)
                        break
                    else:
                        print(f'  -> Messages NOT confirmed as sent (attempt {send_attempt})')
                        try:
                            ActionChains(wb.web_browser).send_keys(Keys.ESCAPE).perform()
                            time.sleep(2)
                        except:
                            pass

                if send_success:
                    sent_wa_links_this_photo.add(wa_link_normalized)
                    with open(photo_sent_messages_fp, 'a') as f:
                        f.write(sent_id + '\n')

                    # Clear from failed tracking if it was there
                    clear_failed_send(sent_id)

                    pending.append({
                        'wa_link': matched_booking['wa_link'],
                        'timestamp_sent': datetime.datetime.now().isoformat(),
                        'booking_code': f"{date_str}_{t}_{sala_label}",
                        'booking_date': matched_booking.get('booking_date', ''),
                        'booking_day': matched_booking.get('booking_day', ''),
                        'booking_time': matched_booking.get('booking_time', ''),
                        'booking_place': matched_booking.get('booking_place', ''),
                    })

                    save_photo_history({
                        'fecha_envio': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'dia_reserva': date_str,
                        'hora_reserva': t,
                        'sala': sala_label,
                        'telefono_cliente': matched_booking['wa_link'],
                        'foto': os.path.basename(photo_path),
                        'resultado': 'ENVIADO',
                    })
                    log_action(
                        booking_code=f"{date_str}_{t}_{sala_label}",
                        phone=matched_booking['wa_link'].split('phone=')[-1],
                        status='ENVIADO',
                        response='sin respuesta',
                        review='-'
                    )
                else:
                    print(f'  -> [WARN] Photo send FAILED after all attempts. Will retry next cycle.')
                    entry_all_sends_ok = False
                    save_photo_history({
                        'fecha_envio': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'dia_reserva': date_str,
                        'hora_reserva': t,
                        'sala': sala_label,
                        'telefono_cliente': matched_booking['wa_link'],
                        'foto': os.path.basename(photo_path),
                        'resultado': 'FALLIDO',
                    })
                    # Track failure across executions
                    attempts = track_failed_send(sent_id, {
                        'date': date_str,
                        'time': t,
                        'sala': sala_label,
                        'wa_link': matched_booking['wa_link'],
                        'phone': matched_booking['wa_link'].split('phone=')[-1],
                        'photo': os.path.basename(photo_path),
                    })
                    log_action(
                        booking_code=f"{date_str}_{t}_{sala_label}",
                        phone=matched_booking['wa_link'].split('phone=')[-1],
                        status=f'FALLIDO (intento {attempts})',
                        response='-',
                        review='-'
                    )

            # After processing all times for this entry: buffer msg_id for later deletion
            if entry_all_sends_ok and sent_wa_links_this_photo:
                msg_id_to_delete = entry.get('msg_id')
                if msg_id_to_delete:
                    sent_photo_msg_ids.append(msg_id_to_delete)
                    print(f'\n  All sends OK for this photo. Queued for deletion after replies are processed.')
          except Exception as entry_err:
            print(f'  -> [ERROR] Failed processing entry: {entry_err}')
            print(f'     Continuing with next entry...')
            try:
                ActionChains(wb.web_browser).send_keys(Keys.ESCAPE).perform()
                time.sleep(2)
            except:
                pass
        json.dump(pending, open(pending_replies_fp, 'w'), indent=2)
        print(f'\n========== MODULE 2 DONE: {len(pending)} pending replies ==========')
        print(f'  Photos queued for deletion: {len(sent_photo_msg_ids)}')
        return sent_photo_msg_ids

    except Exception as e:
        print(f'  [ERROR] match_and_send_photos: {e}, line: {e.__traceback__.tb_lineno}')
        return []


# ============================================================
# MODULE 2B — Photo Audit (Review Image Descriptions)
# ============================================================

def audit_photo_descriptions(photo_entries):
    """
    Audita las imágenes descargadas:
    - Verifica si tienen descripción
    - Valida la nomenclatura
    - Reporta problemas
    """
    try:
        print('\n========== MODULE 2B: Auditing photo descriptions ==========')

        if not photo_entries:
            print('  No photos to audit.')
            return

        audit_results = {
            "total": len(photo_entries),
            "ok": [],
            "no_description": [],
            "bad_nomenclature": []
        }

        for idx, entry in enumerate(photo_entries, 1):
            caption = entry.get('caption', '').strip()
            photo_path = entry.get('photo_path', '?')
            date_str = entry.get('date', '?')
            times = entry.get('times', [])
            salas = entry.get('salas', [])

            print(f'\n  [{idx}] {photo_path}')

            # Check 1: Has description?
            if not caption:
                print(f'      ❌ Sin descripción')
                audit_results["no_description"].append({
                    "photo": photo_path,
                    "date": date_str,
                    "times": times,
                    "salas": salas,
                    "issue": "Imagen sin descripción"
                })
                continue

            # Check 2: Valid nomenclature?
            parsed = parse_photo_caption(caption)
            if not parsed:
                print(f'      ❌ Nomenclatura inválida: "{caption}"')
                print(f'         Debe reenviarse con: DD/MM HH:MM sala')
                audit_results["bad_nomenclature"].append({
                    "photo": photo_path,
                    "caption": caption,
                    "issue": "Nomenclatura inválida - Debe reenviar con: DD/MM HH:MM sala"
                })
                continue

            # Check 3: Image file exists?
            if not os.path.exists(photo_path):
                print(f'      ⚠️  Archivo no encontrado')
                audit_results["no_description"].append({
                    "photo": photo_path,
                    "issue": "Archivo de imagen no encontrado"
                })
                continue

            # All good!
            print(f'      ✅ OK - {caption}')
            audit_results["ok"].append({
                "photo": os.path.basename(photo_path),
                "caption": caption,
                "date": date_str,
                "times": times,
                "salas": salas
            })

        # Print summary
        print(f'\n  ═══════════════════════════════════════════════════')
        print(f'  RESUMEN:')
        print(f'    ✅ Válidas:               {len(audit_results["ok"])}')
        print(f'    ❌ Sin descripción:      {len(audit_results["no_description"])}')
        print(f'    ❌ Nomenclatura inválida: {len(audit_results["bad_nomenclature"])}')
        print(f'  ═══════════════════════════════════════════════════')

        # Save audit results
        audit_fp = os.path.join(sys.path[0], 'data', 'photo_audit_results.json')
        with open(audit_fp, 'w') as f:
            json.dump(audit_results, f, indent=2, ensure_ascii=False)
        print(f'  Reporte guardado en: {audit_fp}')

        print(f'\n========== MODULE 2B DONE ==========')

    except Exception as e:
        print(f'  [ERROR] audit_photo_descriptions: {e}, line: {e.__traceback__.tb_lineno}')


# ============================================================
# MODULE 3 — AI Response Classification
# ============================================================

def _message_has_reaction(msg_element):
    """True if the given message element has any emoji reaction attached."""
    if msg_element is None:
        return False
    selectors = [
        "button[aria-label*='eaccion']",   # matches 'reacción', 'reaccion', 'Reaction'
        "button[aria-label*='eaction']",
        "div[aria-label*='eaccion']",
        "div[aria-label*='eaction']",
        "[data-testid='msg-reaction']",
        "[data-testid*='reaction']",
        "span[data-id*='reaction']",
    ]
    for sel in selectors:
        try:
            if msg_element.find_elements(By.CSS_SELECTOR, sel):
                return True
        except Exception:
            continue
    return False


def _check_recent_outgoing_reactions():
    """Inspect recent out-going messages to detect emoji reactions.

    Returns (photo_has_reaction, text_has_reaction).
    Photo message = has an <img> inside; text message = does not.
    Looks at the last 8 out-going messages to cover photo + thank-you +
    (optional) reminder.
    """
    photo_react = False
    text_react = False
    try:
        outs = wb.web_browser.find_elements(By.CSS_SELECTOR, "div.message-out")
        for msg in outs[-8:]:
            try:
                if not _message_has_reaction(msg):
                    continue
                is_photo = bool(msg.find_elements(By.CSS_SELECTOR, "img[src*='blob'], img[src*='http']"))
                if is_photo:
                    photo_react = True
                else:
                    text_react = True
            except Exception:
                continue
    except Exception:
        pass
    return photo_react, text_react


def _send_positive_review_flow():
    """Send the review photo + review text to the currently-open chat.
    Returns True on success (at least the text was sent)."""
    try:
        with open(positive_review_template_fp, 'r', encoding='utf-8') as f:
            review_msg = f.read().strip()

        if os.path.exists(review_photo_fp):
            print('  -> Copying review photo to clipboard...')
            photo_ps_path = os.path.abspath(review_photo_fp).replace("\\", "/")
            ps_script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$img = [System.Drawing.Image]::FromFile('{photo_ps_path}')
[System.Windows.Forms.Clipboard]::SetImage($img)
$img.Dispose()
"""
            subprocess.run(["powershell", "-STA", "-command", ps_script], check=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
            wb.web_browser.switch_to.window(wb.web_browser.current_window_handle)
            time.sleep(2)

            chat_input = None
            for selector in [
                "footer div[contenteditable='true']",
                "div[contenteditable='true'][data-tab]",
                "div[aria-placeholder='Escribe un mensaje']"
            ]:
                try:
                    chat_input = wb.web_browser.find_element(By.CSS_SELECTOR, selector)
                    wb.web_browser.execute_script("arguments[0].focus(); arguments[0].click();", chat_input)
                    break
                except Exception:
                    continue
            time.sleep(1)

            print('  -> Pasting review photo...')
            if chat_input:
                chat_input.send_keys(Keys.CONTROL, 'v')
            else:
                ActionChains(wb.web_browser).key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform()
            time.sleep(3)

            preview_found = False
            for _ in range(15):
                time.sleep(2)
                try:
                    escribe_inputs = wb.web_browser.find_elements(By.CSS_SELECTOR, "div[aria-placeholder*='Escribe un mensaje']")
                    if len(escribe_inputs) >= 2:
                        preview_found = True
                        break
                except Exception:
                    pass
                for sel in ["span[data-icon='wds-ic-send-filled']", "span[data-icon='pencil']", "span[data-icon='crop']"]:
                    try:
                        if wb.web_browser.find_elements(By.CSS_SELECTOR, sel):
                            preview_found = True
                            break
                    except Exception:
                        continue
                if preview_found:
                    break

            if preview_found:
                for send_sel in ["span[data-icon='send']", "span[data-icon='wds-ic-send-filled']", "div[aria-label='Enviar']"]:
                    try:
                        send_btn = wb.web_browser.find_element(By.CSS_SELECTOR, send_sel)
                        wb.web_browser.execute_script("arguments[0].click();", send_btn)
                        print('  -> Review photo sent!')
                        break
                    except Exception:
                        continue
                time.sleep(5)
            else:
                print('  -> [WARN] Preview not detected for review photo, skipping photo.')
                try:
                    ActionChains(wb.web_browser).send_keys(Keys.ESCAPE).perform()
                    time.sleep(2)
                except Exception:
                    pass
        else:
            print(f'  -> [WARN] Review photo not found at {review_photo_fp}, sending text only.')

        print('  -> Sending review text...')
        chat_input_2 = None
        for selector in [
            "footer div[contenteditable='true']",
            "div[contenteditable='true'][data-tab]",
            "div[aria-placeholder='Escribe un mensaje']"
        ]:
            try:
                chat_input_2 = wb.web_browser.find_element(By.CSS_SELECTOR, selector)
                break
            except Exception:
                continue

        if chat_input_2:
            wb.web_browser.execute_script("arguments[0].focus(); arguments[0].click();", chat_input_2)
            time.sleep(0.5)
            pyperclip.copy(review_msg)
            ActionChains(wb.web_browser).key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform()
            time.sleep(1)
            ActionChains(wb.web_browser).send_keys(Keys.ENTER).perform()
            time.sleep(3)
            print('  -> Review text sent!')
            return True
        else:
            print('  -> [ERROR] Could not find chat input for review text.')
            return False
    except Exception as ex:
        print(f'  [ERROR] Failed to send positive review: {ex}')
        return False


def _send_reminder_to_current_chat():
    """Paste the reminder template into the currently-open chat and verify send.
    Returns (confirmed_bool, fail_reason_str)."""
    try:
        with open(reminder_template_fp, 'r', encoding='utf-8') as f:
            reminder_msg = f.read().strip()
        chat_input = None
        for sel in ["footer div[contenteditable='true']", "div[contenteditable='true'][data-tab]", "div[aria-placeholder='Escribe un mensaje']"]:
            try:
                chat_input = wb.web_browser.find_element(By.CSS_SELECTOR, sel)
                break
            except Exception:
                continue
        if not chat_input:
            return False, 'chat_input_not_found'

        try:
            out_before = len(wb.web_browser.find_elements(By.CSS_SELECTOR, 'div.message-out'))
        except Exception:
            out_before = 0

        wb.web_browser.execute_script("arguments[0].focus(); arguments[0].click();", chat_input)
        time.sleep(0.5)
        pyperclip.copy(reminder_msg)
        ActionChains(wb.web_browser).key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform()
        time.sleep(1)
        ActionChains(wb.web_browser).send_keys(Keys.ENTER).perform()

        for _ in range(10):
            time.sleep(1)
            try:
                out_after = len(wb.web_browser.find_elements(By.CSS_SELECTOR, 'div.message-out'))
            except Exception:
                out_after = out_before
            if out_after > out_before:
                return True, ''
        return False, 'send_not_confirmed'
    except Exception as e:
        return False, f'exception: {e}'


def classify_reply_with_ai(reply_text):
    try:
        url = "https://openrouter.ai/api/v1/chat/completions"
        payload = json.dumps({
            "model": "openai/gpt-4o-mini",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Classify the following customer reply about their escape room "
                        "experience as exactly one word: POSITIVO, NEGATIVO, or NEUTRAL. "
                        "Reply with only that one word."
                    )
                },
                {"role": "user", "content": reply_text}
            ],
            "max_tokens": 10,
            "temperature": 0
        }).encode('utf-8')

        req = urllib.request.Request(url, data=payload, method='POST')
        req.add_header('Content-Type', 'application/json')
        req.add_header('Authorization', f'Bearer {openrouter_api_key}')

        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode('utf-8'))
            classification = result['choices'][0]['message']['content'].strip().upper()
            print(f'  AI classification: {classification}')

            if 'POSITIVO' in classification or 'POSITIVE' in classification:
                return 'POSITIVO'
            elif 'NEGATIVO' in classification or 'NEGATIVE' in classification:
                return 'NEGATIVO'
            else:
                return 'NEUTRAL'

    except Exception as e:
        print(f'  [ERROR] OpenRouter API: {e}')
        return 'NEUTRAL'


def check_pending_replies():
    """Returns list of negative review entries (with booking info) for Module 5."""
    negative_reviews = []
    try:
        print('\n========== MODULE 3: Checking pending replies ==========')

        if not os.path.exists(pending_replies_fp):
            print('  No pending replies file.')
            return negative_reviews

        pending = json.load(open(pending_replies_fp, 'r'))
        if len(pending) == 0:
            print('  No pending replies.')
            return negative_reviews

        updated_pending = []
        now = datetime.datetime.now()

        for entry in pending:
            try:
                sent_time = datetime.datetime.fromisoformat(entry['timestamp_sent'])
                hours_elapsed = (now - sent_time).total_seconds() / 3600

                reminder_sent = entry.get('reminder_sent', False)
                reminder_timestamp_str = entry.get('reminder_timestamp', None)

                # Si la foto fue enviada hace más de 7 días y nunca se mandó el recordatorio
                # → ya es demasiado tarde, se descarta como NEUTRAL
                if not reminder_sent and hours_elapsed > 168:  # 168h = 7 dias
                    print(f'  Entrada expirada (+7 dias sin recordatorio) → NEUTRAL: {entry["booking_code"]}')
                    log_action(
                        booking_code=entry['booking_code'],
                        phone=entry['wa_link'].split('phone=')[-1],
                        status='ENVIADO',
                        response='NEUTRAL (expirado, +7 dias sin recordatorio)',
                        review='-'
                    )
                    continue  # Remove from pending

                # Check if past 48h since reminder → mark neutral and remove
                if reminder_sent and reminder_timestamp_str:
                    reminder_time = datetime.datetime.fromisoformat(reminder_timestamp_str)
                    hours_since_reminder = (now - reminder_time).total_seconds() / 3600
                    if hours_since_reminder >= 48:
                        print(f'  No response 48h after reminder → NEUTRAL: {entry["booking_code"]}')
                        log_action(
                            booking_code=entry['booking_code'],
                            phone=entry['wa_link'].split('phone=')[-1],
                            status='ENVIADO',
                            response='NEUTRAL (sin respuesta 96h)',
                            review='-'
                        )
                        continue  # Remove from pending

                # ------------------------------------------------------------
                # REACTION DETECTION (Jacobo request):
                #   - Emoji reaction on any TEXT message (thanks / reminder)
                #     => treat as POSITIVO, send review link now.
                #   - Emoji reaction on PHOTO only (no reaction on text yet)
                #     => send reminder now (even before 48h), then wait.
                # If neither, fall through to the normal 48h / reply logic.
                # ------------------------------------------------------------
                reaction_handled = False
                try:
                    _open_wa_chat(entry['wa_link'])

                    invalid_markers = [
                        "Enlace incorrecto",
                        "El número de teléfono compartido a través de la dirección URL es inválido",
                        "El número de teléfono compartido a través de la dirección URL no es válido",
                        "no está en WhatsApp",
                    ]
                    if any(m in wb.web_browser.page_source for m in invalid_markers):
                        print(f'  Invalid chat link, removing: {entry["booking_code"]}')
                        continue

                    photo_react, text_react = _check_recent_outgoing_reactions()

                    if text_react:
                        print(f'  -> Reacción EMOJI en mensaje de TEXTO → POSITIVO: {entry["booking_code"]}')
                        if _send_positive_review_flow():
                            log_action(
                                booking_code=entry['booking_code'],
                                phone=entry['wa_link'].split('phone=')[-1],
                                status='ENVIADO',
                                response='POSITIVA (reacción emoji en texto)',
                                review='review enviada'
                            )
                            reaction_handled = True
                            continue  # remove from pending
                        else:
                            print('  -> [FAIL] No se pudo enviar review tras reacción en texto; se reintentará.')
                            updated_pending.append(entry)
                            reaction_handled = True
                            continue

                    if photo_react and not reminder_sent:
                        attempts_so_far = entry.get('reminder_attempts', 0)
                        print(f'  -> Reacción EMOJI sólo en FOTO → recordatorio anticipado: {entry["booking_code"]} (intento {attempts_so_far + 1})')
                        reminder_ok, fail_reason = _send_reminder_to_current_chat()
                        if reminder_ok:
                            entry['reminder_sent'] = True
                            entry['reminder_timestamp'] = now.isoformat()
                            entry['reminder_attempts'] = attempts_so_far + 1
                            entry['reminder_trigger'] = 'photo_reaction'
                            print(f'  -> [OK] Recordatorio CONFIRMADO (gatillo: reacción en foto).')
                            log_action(
                                booking_code=entry['booking_code'],
                                phone=entry['wa_link'].split('phone=')[-1],
                                status='ENVIADO',
                                response='recordatorio enviado (reacción en foto)',
                                review='-'
                            )
                        else:
                            entry['reminder_attempts'] = attempts_so_far + 1
                            entry['reminder_last_fail'] = now.isoformat()
                            entry['reminder_last_fail_reason'] = fail_reason
                            print(f'  -> [FAIL] Recordatorio tras reacción en foto: {fail_reason}. Se reintentará.')
                            log_action(
                                booking_code=entry['booking_code'],
                                phone=entry['wa_link'].split('phone=')[-1],
                                status='ENVIADO',
                                response=f'recordatorio FALLIDO reacción en foto ({fail_reason})',
                                review='-'
                            )
                        updated_pending.append(entry)
                        reaction_handled = True
                        continue
                except Exception as _re:
                    print(f'  [WARN] Chequeo de reacciones falló: {_re}')

                if reaction_handled:
                    continue  # safety net (the branches above already continue)

                # Check if 48h elapsed without response → send reminder
                if hours_elapsed >= 48 and not reminder_sent:
                    attempts_so_far = entry.get('reminder_attempts', 0)
                    print(f'  No response after 48h → sending reminder: {entry["booking_code"]} (attempt {attempts_so_far + 1})')
                    reminder_confirmed = False
                    fail_reason = ''
                    try:
                        chat_input = _open_wa_chat(entry['wa_link'])

                        invalid_markers = [
                            "Enlace incorrecto",
                            "El número de teléfono compartido a través de la dirección URL es inválido",
                            "El número de teléfono compartido a través de la dirección URL no es válido",
                            "no está en WhatsApp",
                        ]
                        if any(m in wb.web_browser.page_source for m in invalid_markers):
                            fail_reason = 'invalid_wa_link'
                            print(f'  -> [FAIL] Invalid WhatsApp link, cannot send reminder.')
                        elif not chat_input:
                            fail_reason = 'chat_input_not_found'
                            print(f'  -> [FAIL] Could not find chat input for reminder.')
                        else:
                            with open(reminder_template_fp, 'r', encoding='utf-8') as f:
                                reminder_msg = f.read().strip()
                            # Count outgoing messages BEFORE sending, so we can
                            # verify a new one actually appeared after Enter.
                            try:
                                out_before = len(wb.web_browser.find_elements(By.CSS_SELECTOR, 'div.message-out'))
                            except:
                                out_before = 0

                            wb.web_browser.execute_script("arguments[0].focus(); arguments[0].click();", chat_input)
                            time.sleep(0.5)
                            pyperclip.copy(reminder_msg)
                            ActionChains(wb.web_browser).key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform()
                            time.sleep(1)
                            ActionChains(wb.web_browser).send_keys(Keys.ENTER).perform()

                            # Verify the message actually appeared in the chat
                            for _check in range(10):
                                time.sleep(1)
                                try:
                                    out_after = len(wb.web_browser.find_elements(By.CSS_SELECTOR, 'div.message-out'))
                                except:
                                    out_after = out_before
                                if out_after > out_before:
                                    reminder_confirmed = True
                                    break
                            if not reminder_confirmed:
                                fail_reason = 'send_not_confirmed'
                                print(f'  -> [FAIL] Reminder Enter pressed but no new message-out detected.')
                    except Exception as re_err:
                        fail_reason = f'exception: {re_err}'
                        print(f'  -> [ERROR] reminder: {re_err}')

                    if reminder_confirmed:
                        print(f'  -> [OK] Reminder CONFIRMED sent!')
                        entry['reminder_sent'] = True
                        entry['reminder_timestamp'] = now.isoformat()
                        entry['reminder_attempts'] = attempts_so_far + 1
                        log_action(
                            booking_code=entry['booking_code'],
                            phone=entry['wa_link'].split('phone=')[-1],
                            status='ENVIADO',
                            response='recordatorio enviado (48h)',
                            review='-'
                        )
                    else:
                        entry['reminder_attempts'] = attempts_so_far + 1
                        entry['reminder_last_fail'] = now.isoformat()
                        entry['reminder_last_fail_reason'] = fail_reason
                        print(f'  -> Will retry next cycle. Total failed attempts: {entry["reminder_attempts"]}')
                        log_action(
                            booking_code=entry['booking_code'],
                            phone=entry['wa_link'].split('phone=')[-1],
                            status='ENVIADO',
                            response=f'recordatorio FALLIDO ({fail_reason})',
                            review='-'
                        )
                    updated_pending.append(entry)
                    continue

                _chat_ready = _open_wa_chat(entry['wa_link'])
                if not _chat_ready:
                    print(f'  No se pudo abrir chat para {entry["booking_code"]}, reintentando próximo ciclo.')
                    updated_pending.append(entry)
                    continue

                invalid_markers = [
                    "Enlace incorrecto",
                    "El número de teléfono compartido a través de la dirección URL es inválido",
                    "El número de teléfono compartido a través de la dirección URL no es válido",
                    "no está en WhatsApp"
                ]
                if any(m in wb.web_browser.page_source for m in invalid_markers):
                    print('  Invalid chat link, removing.')
                    continue

                all_msgs = wb.web_browser.find_elements(
                    By.CSS_SELECTOR, "div.message-in, div.message-out"
                )
                if len(all_msgs) == 0:
                    updated_pending.append(entry)
                    continue

                last_msg = all_msgs[-1]
                msg_classes = last_msg.get_attribute('class') or ''
                if 'message-in' not in msg_classes:
                    print(f'  No reply yet: {entry["booking_code"]}')
                    updated_pending.append(entry)
                    continue

                # Intentar extraer el texto real del mensaje (no metadata/timestamps)
                reply_text = ''
                try:
                    span = last_msg.find_element(
                        By.CSS_SELECTOR,
                        "span.selectable-text, span[class*='selectable-text'], div[class*='copyable-text'] span"
                    )
                    reply_text = (span.get_attribute('innerText') or span.text or '').strip()
                except Exception:
                    pass
                if not reply_text:
                    reply_text = wb.get_text(last_msg)

                if not reply_text or reply_text.strip() == '':
                    print('  Empty reply, keeping.')
                    updated_pending.append(entry)
                    continue

                print(f'  Client reply: {reply_text[:100]}')

                classification = classify_reply_with_ai(reply_text)

                if classification == 'POSITIVO':
                    print('  -> POSITIVE! Sending review photo + link.')
                    with open(positive_review_template_fp, 'r', encoding='utf-8') as f:
                        review_msg = f.read().strip()

                    try:
                        # Step 1: Send review photo if it exists
                        if os.path.exists(review_photo_fp):
                            print('  -> Copying review photo to clipboard...')
                            photo_ps_path = os.path.abspath(review_photo_fp).replace("\\", "/")
                            ps_script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$img = [System.Drawing.Image]::FromFile('{photo_ps_path}')
[System.Windows.Forms.Clipboard]::SetImage($img)
$img.Dispose()
"""
                            subprocess.run(["powershell", "-STA", "-command", ps_script], check=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
                            wb.web_browser.switch_to.window(wb.web_browser.current_window_handle)
                            time.sleep(2)

                            # Find chat input and paste image
                            chat_input = None
                            for selector in [
                                "footer div[contenteditable='true']",
                                "div[contenteditable='true'][data-tab]",
                                "div[aria-placeholder='Escribe un mensaje']"
                            ]:
                                try:
                                    chat_input = wb.web_browser.find_element(By.CSS_SELECTOR, selector)
                                    wb.web_browser.execute_script("arguments[0].focus(); arguments[0].click();", chat_input)
                                    break
                                except:
                                    continue
                            time.sleep(1)

                            print('  -> Pasting review photo...')
                            if chat_input:
                                chat_input.send_keys(Keys.CONTROL, 'v')
                            else:
                                ActionChains(wb.web_browser).key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform()
                            time.sleep(3)

                            # Wait for preview and click send (no caption)
                            preview_found = False
                            for wait_i in range(15):
                                time.sleep(2)
                                try:
                                    escribe_inputs = wb.web_browser.find_elements(By.CSS_SELECTOR, "div[aria-placeholder*='Escribe un mensaje']")
                                    if len(escribe_inputs) >= 2:
                                        preview_found = True
                                        break
                                except:
                                    pass
                                for sel in ["span[data-icon='wds-ic-send-filled']", "span[data-icon='pencil']", "span[data-icon='crop']"]:
                                    try:
                                        if wb.web_browser.find_elements(By.CSS_SELECTOR, sel):
                                            preview_found = True
                                            break
                                    except:
                                        continue
                                if preview_found:
                                    break

                            if preview_found:
                                for send_sel in ["span[data-icon='send']", "span[data-icon='wds-ic-send-filled']", "div[aria-label='Enviar']"]:
                                    try:
                                        send_btn = wb.web_browser.find_element(By.CSS_SELECTOR, send_sel)
                                        wb.web_browser.execute_script("arguments[0].click();", send_btn)
                                        print('  -> Review photo sent!')
                                        break
                                    except:
                                        continue
                                time.sleep(5)
                            else:
                                print('  -> [WARN] Preview not detected for review photo, skipping photo.')
                                try:
                                    ActionChains(wb.web_browser).send_keys(Keys.ESCAPE).perform()
                                    time.sleep(2)
                                except:
                                    pass
                        else:
                            print(f'  -> [WARN] Review photo not found at {review_photo_fp}, sending text only.')

                        # Step 2: Send the review text message
                        print('  -> Sending review text...')
                        chat_input_2 = None
                        for selector in [
                            "footer div[contenteditable='true']",
                            "div[contenteditable='true'][data-tab]",
                            "div[aria-placeholder='Escribe un mensaje']"
                        ]:
                            try:
                                chat_input_2 = wb.web_browser.find_element(By.CSS_SELECTOR, selector)
                                break
                            except:
                                continue

                        if chat_input_2:
                            wb.web_browser.execute_script("arguments[0].focus(); arguments[0].click();", chat_input_2)
                            time.sleep(0.5)
                            pyperclip.copy(review_msg)
                            ActionChains(wb.web_browser).key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform()
                            time.sleep(1)
                            ActionChains(wb.web_browser).send_keys(Keys.ENTER).perform()
                            time.sleep(3)
                            print('  -> Review text sent!')
                            log_action(
                                booking_code=entry['booking_code'],
                                phone=entry['wa_link'].split('phone=')[-1],
                                status='ENVIADO',
                                response='POSITIVA',
                                review='review enviada'
                            )
                        else:
                            print('  -> [ERROR] Could not find chat input for review text.')
                            updated_pending.append(entry)
                            continue
                    except Exception as ex:
                        print(f'  [ERROR] Failed to send positive review: {ex}')
                        updated_pending.append(entry)
                        continue

                elif classification == 'NEGATIVO':
                    print('  -> NEGATIVE. Sending empathy message.')
                    with open(negative_review_template_fp, 'r', encoding='utf-8') as f:
                        neg_msg = f.read().strip()

                    try:
                        chat_input = wb.web_browser.find_element(
                            By.CSS_SELECTOR, "footer div[contenteditable='true']"
                        )
                        wb.web_browser.execute_script("arguments[0].focus(); arguments[0].click();", chat_input)
                        time.sleep(0.5)

                        pyperclip.copy(neg_msg)
                        ActionChains(wb.web_browser).key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform()
                        time.sleep(1)
                        ActionChains(wb.web_browser).send_keys(Keys.ENTER).perform()
                        time.sleep(3)
                        print('  Negative response sent.')
                        log_action(
                            booking_code=entry['booking_code'],
                            phone=entry['wa_link'].split('phone=')[-1],
                            status='ENVIADO',
                            response='NEGATIVA',
                            review='empathy + dequeue'
                        )
                        # Accumulate for Module 5 (dequeue review email in Turitop)
                        negative_reviews.append(entry)
                        print(f'  -> Added to negative reviews queue for Turitop dequeue.')
                    except Exception as ex:
                        print(f'  [ERROR] Failed to send negative empathy msg: {ex}')
                        updated_pending.append(entry)
                        continue

                else:
                    # NEUTRAL: save for manual review instead of silently dropping.
                    # This also catches API failures (which return NEUTRAL).
                    print(f'  -> NEUTRAL. Saving to neutral_replies.json for manual review.')
                    try:
                        neutral_list = []
                        if os.path.exists(neutral_replies_fp):
                            with open(neutral_replies_fp, 'r', encoding='utf-8') as _nf:
                                neutral_list = json.load(_nf)
                        neutral_list.append({
                            'timestamp': now.isoformat(),
                            'booking_code': entry.get('booking_code', ''),
                            'phone': entry['wa_link'].split('phone=')[-1],
                            'wa_link': entry['wa_link'],
                            'reply_text': reply_text,
                            'ai_classification': 'NEUTRAL',
                        })
                        # Keep last 200 entries
                        neutral_list = neutral_list[-200:]
                        with open(neutral_replies_fp, 'w', encoding='utf-8') as _nf:
                            json.dump(neutral_list, _nf, indent=2, ensure_ascii=False)
                        print(f'  -> Guardado: {neutral_replies_fp}')
                    except Exception as _ne:
                        print(f'  -> [ERROR] saving neutral reply: {_ne}')
                    log_action(
                        booking_code=entry['booking_code'],
                        phone=entry['wa_link'].split('phone=')[-1],
                        status='ENVIADO',
                        response='NEUTRAL (guardado para revisión)',
                        review='-'
                    )

            except Exception as e_entry:
                print(f'  [ERROR] {e_entry}, line: {e_entry.__traceback__.tb_lineno}')
                updated_pending.append(entry)

        json.dump(updated_pending, open(pending_replies_fp, 'w'), indent=2)
        print(f'\n========== MODULE 3 DONE: {len(updated_pending)} still pending ==========')
        if negative_reviews:
            print(f'  -> {len(negative_reviews)} negative reviews queued for Turitop dequeue.')
        return negative_reviews

    except Exception as e:
        print(f'  [ERROR] check_pending_replies: {e}, line: {e.__traceback__.tb_lineno}')
        return negative_reviews


# ============================================================
# MODULE 5 — Dequeue Turitop review emails for negative reviews
# ============================================================

def dequeue_negative_review_emails(negative_entries):
    """For each negative review, go to Turitop, find the booking,
    click 'Acciones del Email' and select 'Desencolar el email de solicitud de opinión automática'.

    Uses the actual Turitop HTML structure:
    - Email button: a.tobj_icon_send_email with onclick="sendConfirmationMail(BOOKING_ID, 'SHORT_ID', 'show')"
    - AJAX menu loads into #booking_actions_container_BOOKING_ID
    - Menu contains select[name='mailMenuOption'] with desencolar option
    """
    try:
        print(f'\n========== MODULE 5: Dequeuing {len(negative_entries)} review emails ==========')

        if not negative_entries:
            print('  No negative reviews to process.')
            print('========== MODULE 5 DONE ==========')
            return

        # Open Turitop in a new tab
        wb.web_browser.execute_script("window.open('')")
        wb.web_browser.switch_to.window(wb.web_browser.window_handles[-1])
        wb.get("https://app.turitop.com/admin/company/P271/bookings")
        time.sleep(5)

        if "admin/login/es/" in wb.web_browser.current_url:
            print("  [WARN] Turitop logged out! Cannot dequeue emails.")
            wb.web_browser.close()
            wb.web_browser.switch_to.window(wb.web_browser.window_handles[0])
            return

        for entry in negative_entries:
            try:
                booking_code = entry.get('booking_code', '')
                booking_day = entry.get('booking_day', '')
                booking_time = entry.get('booking_time', '')
                booking_place = entry.get('booking_place', '')

                # FALLBACK: Parse from booking_code if fields are empty
                # booking_code format: "26/3_17:30_4e" or "26/3_16:00_csi/maf"
                code_parts = booking_code.split('_')
                if len(code_parts) >= 3:
                    code_date = code_parts[0]       # "26/3"
                    code_time = code_parts[1]       # "17:30"
                    code_sala = code_parts[2]        # "4e" or "csi/maf"
                elif len(code_parts) >= 2:
                    code_date = code_parts[0]
                    code_time = code_parts[1]
                    code_sala = ''
                else:
                    print(f'  -> Invalid booking_code format: {booking_code}')
                    continue

                date_parts = code_date.split('/')
                if len(date_parts) != 2:
                    print(f'  -> Cannot parse date from booking_code: {booking_code}')
                    continue
                day = date_parts[0]
                month = date_parts[1]

                # Use fallback values from booking_code when fields are empty
                if not booking_time:
                    booking_time = code_time
                if not booking_day:
                    booking_day = day
                if not booking_place and code_sala:
                    # Convert sala abbreviation to place names
                    sala_parts = code_sala.split('/')
                    place_names = []
                    for s in sala_parts:
                        place_names.extend(sala_to_places.get(s.lower().strip(), []))
                    booking_place = ','.join(place_names) if place_names else code_sala

                print(f'\n  Processing: {booking_code} (day={booking_day} time={booking_time} place={booking_place})')

                year = datetime.datetime.now().year
                target_date = f"{int(day):02d}-{int(month):02d}-{year}"

                # Navigate to bookings and filter by date
                wb.get("https://app.turitop.com/admin/company/P271/bookings")
                time.sleep(5)

                wb.send_keys("#filter_event_date_from", target_date, full=True, clear=True)
                wb.send_keys("#filter_event_date_to", target_date, full=True, clear=True)
                time.sleep(2)
                wb.js_click("button[type=submit][name=action]")
                time.sleep(5)

                # Find the correct booking row by matching day, time and place
                rows = wb.web_browser.find_elements(
                    By.CSS_SELECTOR, "tr.bookings-history-row:not([class*='deleted'])"
                )

                print(f'  -> Found {len(rows)} booking rows for date {target_date}')

                target_row = None
                for row in rows:
                    try:
                        row_day_el = row.find_element(By.CSS_SELECTOR, "div.format-day-of-month-number")
                        row_time_el = row.find_element(By.CSS_SELECTOR, "div.format-time-short")
                        row_place_el = row.find_element(By.CSS_SELECTOR, "span.bookings-product-name")
                        row_day = row_day_el.text.strip()
                        row_time = row_time_el.text.strip()
                        row_place = row_place_el.text.strip()

                        print(f'     Row: day={row_day} time={row_time} place={row_place}')

                        # Match by time and day
                        if row_time == booking_time.strip() and row_day == day.strip():
                            # If we have place info, verify it matches
                            if booking_place:
                                place_list = [p.strip().upper() for p in booking_place.split(',')]
                                row_place_upper = row_place.upper().replace('#', '')
                                matched = False
                                for p in place_list:
                                    if p.replace('#', '') in row_place_upper:
                                        matched = True
                                        break
                                if matched:
                                    target_row = row
                                    break
                            else:
                                target_row = row
                                break
                    except Exception as row_err:
                        continue

                if not target_row:
                    print(f'  -> Booking row not found in Turitop.')
                    continue

                print(f'  -> Found booking row. Looking for email action button...')

                # Extract booking_id from row id attribute (e.g. "history_row_49257387")
                row_id = target_row.get_attribute('id') or ''
                booking_id = row_id.replace('history_row_', '') if 'history_row_' in row_id else ''

                # Extract short_id from hidden input in the row
                short_id = ''
                try:
                    hash_input = target_row.find_element(By.CSS_SELECTOR, "input[id^='hashReservationId_']")
                    short_id = hash_input.get_attribute('value') or ''
                except:
                    pass

                # Also try to get booking_id and short_id from the email button's onclick
                email_btn = None
                try:
                    email_btn = target_row.find_element(By.CSS_SELECTOR, "a.tobj_icon_send_email")
                    onclick_attr = email_btn.get_attribute('onclick') or ''
                    print(f'  -> Email button onclick: {onclick_attr}')
                    # Parse: sendConfirmationMail(49257387, 'P271-260324-7', 'show');
                    import re
                    match = re.search(r"sendConfirmationMail\((\d+),\s*'([^']+)',\s*'show'\)", onclick_attr)
                    if match:
                        booking_id = match.group(1)
                        short_id = match.group(2)
                except Exception as btn_err:
                    print(f'  -> Could not find a.tobj_icon_send_email: {btn_err}')
                    # Fallback: try other selectors
                    try:
                        email_btn = target_row.find_element(By.CSS_SELECTOR, "a[onclick*='sendConfirmationMail']")
                        onclick_attr = email_btn.get_attribute('onclick') or ''
                        match = re.search(r"sendConfirmationMail\((\d+),\s*'([^']+)',\s*'show'\)", onclick_attr)
                        if match:
                            booking_id = match.group(1)
                            short_id = match.group(2)
                    except:
                        pass

                if not booking_id or not short_id:
                    print(f'  -> [WARN] Could not extract booking_id={booking_id} or short_id={short_id}')
                    continue

                print(f'  -> booking_id={booking_id}, short_id={short_id}')
                print(f'  -> Calling sendConfirmationMail via JS...')

                # Execute the sendConfirmationMail function directly via JavaScript
                wb.web_browser.execute_script(
                    f"sendConfirmationMail({booking_id}, '{short_id}', 'show');"
                )
                print(f'  -> Waiting 10s for email actions menu to load...')
                time.sleep(10)

                # The AJAX menu loads into #booking_actions_container_BOOKING_ID
                container_id = f"booking_actions_container_{booking_id}"
                dequeue_clicked = False

                try:
                    container = wb.web_browser.find_element(By.ID, container_id)
                    print(f'  -> Found actions container #{container_id}')

                    # Find the select[name='mailMenuOption'] inside the container
                    select_el = container.find_element(By.CSS_SELECTOR, "select[name='mailMenuOption']")
                    from selenium.webdriver.support.ui import Select
                    select_obj = Select(select_el)

                    # List all options for debugging
                    for opt in select_obj.options:
                        opt_text = opt.text.strip()
                        print(f'     Option: "{opt_text}"')

                    # Find and select the desencolar option
                    for option in select_obj.options:
                        opt_text = (option.text or '').strip().lower()
                        if 'desencolar' in opt_text:
                            select_obj.select_by_visible_text(option.text.strip())
                            dequeue_clicked = True
                            print(f'  -> Selected "Desencolar" option from dropdown.')
                            break

                except Exception as container_err:
                    print(f'  -> Error finding actions container or select: {container_err}')
                    # Fallback: try finding any select with desencolar option on the page
                    try:
                        all_selects = wb.web_browser.find_elements(By.CSS_SELECTOR, "select[name='mailMenuOption']")
                        for sel in all_selects:
                            select_obj = Select(sel)
                            for option in select_obj.options:
                                opt_text = (option.text or '').strip().lower()
                                if 'desencolar' in opt_text:
                                    select_obj.select_by_visible_text(option.text.strip())
                                    dequeue_clicked = True
                                    print(f'  -> Selected "Desencolar" from fallback select.')
                                    break
                            if dequeue_clicked:
                                break
                    except:
                        pass

                if dequeue_clicked:
                    time.sleep(2)

                    # Click the "Confirmar" button which calls deleteSurvey()
                    # The real HTML has: <a class="btn submit5 add" onclick="deleteSurvey(ID, 'SHORT_ID', 'send');">Confirmar</a>
                    confirm_clicked = False

                    # Method 1: Call deleteSurvey() directly via JavaScript (most reliable)
                    try:
                        print(f'  -> Calling deleteSurvey({booking_id}, {short_id}, send) via JS...')
                        wb.web_browser.execute_script(
                            f"deleteSurvey({booking_id}, '{short_id}', 'send');"
                        )
                        confirm_clicked = True
                        print(f'  -> deleteSurvey() called successfully.')
                    except Exception as js_err:
                        print(f'  -> JS deleteSurvey failed: {js_err}')

                    # Method 2: Fallback - find and click the "Confirmar" <a> button in the container
                    if not confirm_clicked:
                        try:
                            container = wb.web_browser.find_element(By.ID, container_id)
                            # The button is an <a> with class "btn submit5 add" inside .unqueueSurveyOptions
                            confirm_btn = container.find_element(
                                By.CSS_SELECTOR,
                                ".unqueueSurveyOptions a.btn"
                            )
                            wb.web_browser.execute_script("arguments[0].click();", confirm_btn)
                            confirm_clicked = True
                            print(f'  -> Clicked "Confirmar" button via CSS selector.')
                        except Exception as btn_err:
                            print(f'  -> Fallback button click failed: {btn_err}')

                    # Method 3: Last resort - find any <a> with text "Confirmar" in the container
                    if not confirm_clicked:
                        try:
                            container = wb.web_browser.find_element(By.ID, container_id)
                            all_links = container.find_elements(By.TAG_NAME, "a")
                            for link in all_links:
                                if 'confirmar' in (link.text or '').strip().lower():
                                    wb.web_browser.execute_script("arguments[0].click();", link)
                                    confirm_clicked = True
                                    print(f'  -> Clicked "Confirmar" link by text match.')
                                    break
                        except:
                            pass

                    if confirm_clicked:
                        time.sleep(5)

                        # Check for confirmation dialog and accept
                        try:
                            alert = wb.web_browser.switch_to.alert
                            alert.accept()
                            print(f'  -> Confirmation alert accepted.')
                        except:
                            pass

                        time.sleep(3)
                        print(f'  -> [OK] Review email dequeued for {booking_code}!')
                    else:
                        print(f'  -> [WARN] Could not click "Confirmar" button for {booking_code}.')
                else:
                    print(f'  -> [WARN] Could not find "Desencolar" option in the menu.')

            except Exception as e_entry:
                print(f'  [ERROR] Processing negative entry: {e_entry}, line: {e_entry.__traceback__.tb_lineno}')

        # Close Turitop tab
        try:
            wb.web_browser.close()
            wb.web_browser.switch_to.window(wb.web_browser.window_handles[0])
        except:
            pass

        print(f'\n========== MODULE 5 DONE ==========')

    except Exception as e:
        print(f'  [ERROR] dequeue_negative_review_emails: {e}, line: {e.__traceback__.tb_lineno}')
        try:
            if len(wb.web_browser.window_handles) > 1:
                wb.web_browser.close()
                wb.web_browser.switch_to.window(wb.web_browser.window_handles[0])
        except:
            pass


# ============================================================
# MODULE 7 — Notify photo group of repeated send failures
# ============================================================

def notify_group_failed_sends():
    """If any sends have failed 2+ times and not yet notified, send an alert
    to the photo group with the list of problematic photos."""
    try:
        failed = _load_failed_sends()
        to_notify = {sid: info for sid, info in failed.items()
                     if info.get('attempts', 0) >= 2 and not info.get('notified', False)}

        if not to_notify:
            return

        print(f'\n========== MODULE 7: Notifying group of {len(to_notify)} repeated failures ==========')

        # Build alert message
        lines = ['🚨 ALERTA - Fotos no enviadas (2+ intentos):', '']
        for sid, info in to_notify.items():
            bi = info.get('booking_info', {})
            first = info.get('first_failure', '')[:16].replace('T', ' ')
            attempts = info.get('attempts', 0)
            lines.append(
                f"❌ {bi.get('date', '?')} {bi.get('time', '?')} {bi.get('sala', '?')} "
                f"→ {bi.get('phone', '?')}"
            )
            lines.append(f"   Intentos: {attempts} | Primer fallo: {first}")
            lines.append('')
        lines.append('Por favor revisar manualmente.')
        alert_msg = '\n'.join(lines)

        # Open photo group and send the alert
        print(f'  Opening photo group: {photo_group_name}')
        try:
            search_box = wb.web_browser.find_element(
                By.CSS_SELECTOR, "div[contenteditable='true'][data-tab='3']"
            )
        except:
            try:
                search_box = wb.web_browser.find_element(
                    By.CSS_SELECTOR, "div[role='textbox'][contenteditable='true']"
                )
            except:
                print('  [ERROR] Could not find search box.')
                return

        search_box.click()
        time.sleep(1)
        pyperclip.copy(photo_group_name)
        ActionChains(wb.web_browser).key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform()
        time.sleep(2)

        try:
            group_elem = wb.web_browser.find_element(
                By.XPATH, f"//span[@title='{photo_group_name}']"
            )
            group_elem.click()
            time.sleep(3)
        except:
            print('  [ERROR] Could not find photo group in search results.')
            ActionChains(wb.web_browser).send_keys(Keys.ESCAPE).perform()
            return

        # Find chat input and send the alert
        chat_input = None
        for sel in [
            "footer div[contenteditable='true']",
            "div[contenteditable='true'][data-tab]",
            "div[aria-placeholder='Escribe un mensaje']",
        ]:
            try:
                chat_input = wb.web_browser.find_element(By.CSS_SELECTOR, sel)
                break
            except:
                continue

        if not chat_input:
            print('  [ERROR] Could not find chat input in group.')
            return

        wb.web_browser.execute_script("arguments[0].focus(); arguments[0].click();", chat_input)
        time.sleep(0.5)
        pyperclip.copy(alert_msg)
        ActionChains(wb.web_browser).key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform()
        time.sleep(1)
        ActionChains(wb.web_browser).send_keys(Keys.ENTER).perform()
        time.sleep(2)
        print(f'  -> Alert sent to group with {len(to_notify)} failures.')

        # Mark all as notified
        for sid in to_notify:
            failed[sid]['notified'] = True
        _save_failed_sends(failed)

        print('========== MODULE 7 DONE ==========')

    except Exception as e:
        print(f'  [ERROR] notify_group_failed_sends: {e}, line: {e.__traceback__.tb_lineno}')


# ============================================================
# MODULE 4 — Daily Cleanup
# ============================================================

def daily_cleanup():
    try:
        print('\n========== MODULE 4: Daily cleanup ==========')
        now = datetime.datetime.now()

        if os.path.exists(downloaded_photos_dir):
            for photo_file in os.listdir(downloaded_photos_dir):
                fp = os.path.join(downloaded_photos_dir, photo_file)
                if os.path.isfile(fp):
                    file_age_hours = (time.time() - os.path.getmtime(fp)) / 3600
                    if file_age_hours > 24:
                        os.remove(fp)
                        print(f'  Cleaned: {photo_file}')

        if os.path.exists(pending_replies_fp):
            pending = json.load(open(pending_replies_fp, 'r'))
            updated = []
            for entry in pending:
                try:
                    sent_time = datetime.datetime.fromisoformat(entry['timestamp_sent'])
                    hours_elapsed = (now - sent_time).total_seconds() / 3600
                    if hours_elapsed <= 96:
                        updated.append(entry)
                    else:
                        print(f'  Removed expired (>96h): {entry["booking_code"]}')
                except:
                    pass

            if len(updated) != len(pending):
                json.dump(updated, open(pending_replies_fp, 'w'), indent=2)
                print(f'  Cleaned {len(pending) - len(updated)} expired entries.')

        print('========== MODULE 4 DONE ==========')

    except Exception as e:
        print(f'  [ERROR] daily_cleanup: {e}, line: {e.__traceback__.tb_lineno}')


# ============================================================
# MAIN — Test flow
# ============================================================

AUTO_MODE = '--auto' in sys.argv  # Pass --auto to skip the "Press Enter" pause

print("\n" + "=" * 60)
print("  TEST PHOTO MODULES - Script independiente")
print("=" * 60)

print("\nStarting browser...")
wb = Browser()

print("Opening WhatsApp Web...")
wb.get("https://web.whatsapp.com")
# Dismiss Chrome's "Restore pages?" dialog if present
time.sleep(2)
wb.dismiss_restore_dialog()

print("Esperando conexion de WhatsApp (max 1 minuto)...")
_wa_connected = False
_wa_timeout = 60  # seconds — si sesion ya activa conecta en 2-3s
_wa_start = time.time()
_qr_shown = False
while time.time() - _wa_start < _wa_timeout:
    try:
        src = wb.web_browser.page_source
        if 'data-testid="qrcode"' in src or 'data-ref=' in src:
            if not _qr_shown:
                print("  >> QR visible — escanea con tu movil ahora (tienes ~55s) <<")
                _qr_shown = True
        elif any(x in src for x in ['data-testid="chat-list"', 'data-testid="conversation-panel"',
                                     '_pn_list_', 'side', 'aria-label="Lista de chats"']):
            _wa_connected = True
            elapsed = int(time.time() - _wa_start)
            print(f"  WhatsApp conectado OK ({elapsed}s)")
            break
    except Exception:
        pass
    time.sleep(2)

if not _wa_connected:
    print("\n[ERROR] WhatsApp no se conecto en 1 minuto.")
    print("  Posibles causas: QR no escaneado, sesion caducada, sin internet.")
    print("  Cerrando. Vuelve a ejecutar y escanea el QR cuando aparezca.")
    wb.web_browser.quit()
    sys.exit(1)

print("\n--- Running all photo modules ---\n")

_cycle_errors = []
_cycle_stats = {'fotos_nuevas': 0, 'envios_ok': 0, 'envios_fail': 0, 'reviews_neg': 0, 'fotos_borradas': 0}

# Module 4: Cleanup first
daily_cleanup()

# Module 1: Scrape photo group
photo_entries = scrape_photo_group()
_cycle_stats['fotos_nuevas'] = len(photo_entries) if photo_entries else 0

# Module 2: Match and send (returns buffer of msg_ids to delete later)
photos_to_delete = []
if photo_entries:
    photos_to_delete = match_and_send_photos(photo_entries) or []
else:
    print("\nNo photo entries to match — skipping Module 2.")

# Module 2B: Audit photo descriptions
if photo_entries:
    audit_photo_descriptions(photo_entries)

# Module 3: Check pending replies
negative_reviews = check_pending_replies() or []
_cycle_stats['reviews_neg'] = len(negative_reviews) if negative_reviews else 0

# Module 5: Dequeue review emails for negative reviews
if negative_reviews:
    dequeue_negative_review_emails(negative_reviews)
else:
    print("\nNo negative reviews — skipping Module 5.")

# Module 6: Delete sent photos from group (only after all modules are done)
deleted_count = 0
if photos_to_delete:
    deleted_count = delete_sent_photos_from_group(photos_to_delete) or 0
else:
    print("\nNo photos to delete from group — skipping Module 6.")
_cycle_stats['fotos_borradas'] = deleted_count

# Module 7: Notify group of repeated send failures (2+ attempts)
notify_group_failed_sends()

# Count sends from photo_history
try:
    if os.path.exists(photo_history_fp):
        with open(photo_history_fp, 'r', encoding='utf-8') as _hf:
            _history = json.load(_hf)
        _today = datetime.datetime.now().strftime('%Y-%m-%d')
        _today_entries = [h for h in _history if h.get('fecha_envio', '').startswith(_today)]
        _cycle_stats['envios_ok'] = sum(1 for h in _today_entries if h['resultado'] == 'ENVIADO')
        _cycle_stats['envios_fail'] = sum(1 for h in _today_entries if h['resultado'] == 'FALLIDO')
except:
    pass

# Write detailed session log
write_session_log()

# Write execution log
_log_details = (
    f"Fotos nuevas: {_cycle_stats['fotos_nuevas']} | "
    f"Envios OK: {_cycle_stats['envios_ok']} | "
    f"Envios FAIL: {_cycle_stats['envios_fail']} | "
    f"Reviews negativas: {_cycle_stats['reviews_neg']} | "
    f"Fotos borradas: {_cycle_stats['fotos_borradas']}"
)
_result = "NEGATIVO" if _cycle_stats['envios_fail'] > 0 else "POSITIVO"
write_execution_log(_result, _log_details)

print("\n" + "=" * 60)
print("  ALL DONE!")
print("=" * 60)
wb.web_browser.quit()
sys.exit(0)
