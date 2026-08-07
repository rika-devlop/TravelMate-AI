import streamlit as st
from datetime import datetime, timedelta
from google import genai
from fpdf import FPDF
import re
import json
import os
import glob
import random
import csv

# ------------------------------------------------------------------
# App config
# ------------------------------------------------------------------
st.set_page_config(page_title="TravelMate AI", page_icon="🌍", layout="wide")

def get_random_greeting(name):
    greetings = [
        f"Welcome {name} 👋",
        f"Hi {name} 👋",
        f"Hello {name} ✨",
        f"Great to see you, {name} 😊",
        f"Hey {name} 🌍",
        f"Welcome back, {name} 🎉",
    ]
    return random.choice(greetings)


# ------------------------------------------------------------------
# Persist login across refreshes using query params
# ------------------------------------------------------------------
def check_login_from_params():
    """Check if user info is in URL params and restore session"""
    query_params = st.query_params
    if "user_id" in query_params and "user_name" in query_params and "user_email" in query_params:
        st.session_state.current_user_id = query_params["user_id"]
        st.session_state.preferred_name = query_params["user_name"]
        st.session_state.email_id = query_params["user_email"]
        st.session_state.greeting_text = get_random_greeting(query_params["user_name"])
        st.session_state.logged_in = True
        return True
    return False

# ------------------------------------------------------------------
# File Paths & Database Config
# ------------------------------------------------------------------
BASE_PLAN_DIR = "travelplan"
USERS_CSV = "users.csv"

# Ensure the base directory exists
os.makedirs(BASE_PLAN_DIR, exist_ok=True)

# ------------------------------------------------------------------
# API client
# ------------------------------------------------------------------
API_KEY = "AQ.Ab8RN6LZW9aq5T4ysIPMnBSRI-25XaEb6VuXM8yhGCsP6cMFrg"
client = genai.Client(api_key=API_KEY)

# ------------------------------------------------------------------
# Session state initialization
# ------------------------------------------------------------------
# Initialize session state BEFORE login check
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "current_user_id" not in st.session_state:
    st.session_state.current_user_id = None
if "preferred_name" not in st.session_state:
    st.session_state.preferred_name = ""
if "email_id" not in st.session_state:
    st.session_state.email_id = ""
if "greeting_text" not in st.session_state:
    st.session_state.greeting_text = ""
if "generated_itinerary" not in st.session_state:
    st.session_state.generated_itinerary = None
if "generated_pdf_bytes" not in st.session_state:
    st.session_state.generated_pdf_bytes = None
if "generated_title" not in st.session_state:
    st.session_state.generated_title = None
if "viewing_saved_itinerary" not in st.session_state:
    st.session_state.viewing_saved_itinerary = None
if "current_download_filename" not in st.session_state:
    st.session_state.current_download_filename = "itinerary.pdf"

# Check if user should be logged in from URL params
check_login_from_params()

# ------------------------------------------------------------------
# User Management (CSV)
# ------------------------------------------------------------------
def init_users_db():
    if not os.path.exists(USERS_CSV):
        with open(USERS_CSV, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(["uniqueid", "name", "emailid"])


def get_or_create_user(name, email):
    email = email.strip().lower()
    name = name.strip()
    users = []
    max_id = 0

    init_users_db()

    # Read existing users
    with open(USERS_CSV, mode='r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            users.append(row)
            try:
                uid = int(row["uniqueid"])
                if uid > max_id:
                    max_id = uid
            except ValueError:
                pass

    # Check if user already exists
    for u in users:
        if u["emailid"] == email:
            # Ensure their personal directory exists
            user_dir = os.path.join(BASE_PLAN_DIR, str(u["uniqueid"]))
            os.makedirs(user_dir, exist_ok=True)
            return str(u["uniqueid"]), u["name"]

    # Create new user
    new_id = str(max_id + 1)
    with open(USERS_CSV, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([new_id, name, email])

    # Create their personal directory
    user_dir = os.path.join(BASE_PLAN_DIR, new_id)
    os.makedirs(user_dir, exist_ok=True)

    return new_id, name


# ------------------------------------------------------------------
# Usage Limit Management
# ------------------------------------------------------------------
DAILY_LIMIT = 5
USAGE_LOG_FILE = "usage_log.json"


def init_usage_log():
    """Initialize usage log file if it doesn't exist"""
    if not os.path.exists(USAGE_LOG_FILE):
        with open(USAGE_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)


def get_today_date():
    """Get today's date in YYYY-MM-DD format"""
    return datetime.now().strftime("%Y-%m-%d")


def get_user_usage_count(email_id, user_id):
    """Get how many times a user has generated itineraries today"""
    init_usage_log()

    with open(USAGE_LOG_FILE, "r", encoding="utf-8") as f:
        log = json.load(f)

    today = get_today_date()
    key = f"{email_id}_{today}"

    return log.get(key, 0)


def increment_usage_count(email_id, user_id):
    """Increment daily usage count for user"""
    init_usage_log()

    with open(USAGE_LOG_FILE, "r", encoding="utf-8") as f:
        log = json.load(f)

    today = get_today_date()
    key = f"{email_id}_{today}"

    log[key] = log.get(key, 0) + 1

    with open(USAGE_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2)


def is_user_eligible_for_generation(user_id, email_id):
    """
    Check if user can generate an itinerary today.
    Users with ID < 100 have unlimited access.
    Users with ID >= 100 have 5 itineraries per day limit.
    """
    try:
        user_id_int = int(user_id)
    except:
        return True

    # Users with ID < 100 have unlimited access
    if user_id_int < 100:
        return True

    # Users with ID >= 100 have daily limit
    usage_count = get_user_usage_count(email_id, user_id)
    return usage_count < DAILY_LIMIT


def get_remaining_count(user_id, email_id):
    """Get remaining itinerary generations for today"""
    try:
        user_id_int = int(user_id)
    except:
        return -1  # Unlimited

    if user_id_int < 100:
        return -1  # Unlimited (show as unlimited)

    usage_count = get_user_usage_count(email_id, user_id)
    remaining = DAILY_LIMIT - usage_count
    return max(0, remaining)


# ------------------------------------------------------------------
# Helper: load / save / delete itinerary records
# ------------------------------------------------------------------
def get_user_dir(user_id):
    return os.path.join(BASE_PLAN_DIR, str(user_id))


def load_saved_itineraries(user_id):
    user_dir = get_user_dir(user_id)
    records = []
    if not os.path.exists(user_dir):
        return records

    for fpath in sorted(glob.glob(os.path.join(user_dir, "*.json")), reverse=True):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            records.append(data)
        except Exception:
            continue
    return records


def save_itinerary_record(user_id, pdf_bytes, itinerary_text, title, group_size, dates, dest, start_date_str):
    user_dir = get_user_dir(user_id)
    os.makedirs(user_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_dest = re.sub(r"[^a-zA-Z0-9]+", "_", dest[:30]).strip("_")
    if not safe_dest:
        safe_dest = "destination"
    base = f"{timestamp}_{safe_dest}"

    pdf_path = os.path.join(user_dir, f"{base}.pdf")
    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)

    filename = f"TravelMate_{safe_dest}_{start_date_str.replace(' ', '_')}.pdf"
    json_path = os.path.join(user_dir, f"{base}.json")

    record = {
        "title": title,
        "group_size": group_size,
        "dates": dates,
        "filename": filename,
        "pdf_path": pdf_path,
        "json_path": json_path,
        "created": datetime.now().isoformat(),
        "dest": dest,
        "start_date": start_date_str,
        "itinerary_text": itinerary_text,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)

    return record


def delete_itinerary_record(pdf_path, json_path):
    try:
        if pdf_path and os.path.exists(pdf_path):
            os.remove(pdf_path)
        if json_path and os.path.exists(json_path):
            os.remove(json_path)
    except Exception:
        pass



# ------------------------------------------------------------------
# PDF generator
# ------------------------------------------------------------------
def create_pdf(itinerary_text, start_addr, dest_addr, num_people, days, start_date_str):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    def clean_text(txt):
        if not txt:
            return ""
        txt = re.sub(r"^#{1,6}\s*", "", txt, flags=re.MULTILINE)
        txt = txt.replace("**", "").replace("*", "")
        replacements = {
            "\u2013": "-", "\u2014": " - ", "\u2018": "'", "\u2019": "'",
            "\u201c": '"', "\u201d": '"', "\u2022": "-", "\u2026": "...", "•": "-",
        }
        for old, new in replacements.items():
            txt = txt.replace(old, new)
        txt = re.sub(r"\s+", " ", txt).strip()
        return txt.encode("ascii", "ignore").decode("ascii")

    def is_table_line(line):
        stripped = line.strip()
        if not stripped: return False
        if stripped.count("|") < 2: return False
        if re.match(r"^[\s\|:\-]+$", stripped): return True
        return stripped.startswith("|") and stripped.endswith("|")

    def parse_table_row(line):
        stripped = line.strip().strip("|")
        return [cell.strip() for cell in stripped.split("|")]

    def render_table(headers, rows):
        available_width = pdf.w - 2 * pdf.l_margin
        num_cols = len(headers)
        col_widths = [available_width / num_cols] * num_cols
        line_height = 7

        pdf.set_fill_color(25, 55, 109)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Arial", "B", 9)
        for i, header in enumerate(headers):
            pdf.cell(col_widths[i], line_height, clean_text(header), border=1, align="C", fill=True)
        pdf.ln()

        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Arial", "", 9)
        for row_idx, row in enumerate(rows):
            pdf.set_fill_color(245, 248, 252)
            for i, cell in enumerate(row):
                align = "L" if i == 0 else "C"
                pdf.cell(col_widths[i], line_height, clean_text(cell), border=1, align=align)
            pdf.ln()
        pdf.ln(3)

    pdf.set_fill_color(25, 55, 109)
    pdf.rect(0, 0, 210, 40, "F")
    pdf.set_y(12)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Arial", "B", 20)
    pdf.cell(0, 10, "TRAVEL ITINERARY", ln=1, align="C")
    pdf.set_font("Arial", "", 12)
    pdf.cell(0, 8, clean_text(f"{start_addr} to {dest_addr}"), ln=1, align="C")

    pdf.set_y(45)
    pdf.set_text_color(40, 40, 40)
    pdf.set_font("Arial", "B", 10)
    info = f"Group: {num_people} | Duration: {days} Days | Start: {start_date_str}"
    pdf.cell(0, 10, clean_text(info), ln=1, align="C")
    pdf.ln(5)

    lines = itinerary_text.split("\n")
    i = 0
    while i < len(lines):
        raw_line = lines[i].strip()

        if (is_table_line(raw_line) and i + 1 < len(lines)
                and is_table_line(lines[i + 1].strip())
                and re.match(r"^[\s\|:\-]+$", lines[i + 1].strip())):
            headers = parse_table_row(raw_line)
            i += 2
            rows = []
            while i < len(lines) and is_table_line(lines[i].strip()):
                row = parse_table_row(lines[i].strip())
                if row:
                    while len(row) < len(headers): row.append("")
                    rows.append(row[: len(headers)])
                i += 1
            render_table(headers, rows)
            continue

        if not raw_line:
            pdf.ln(2)
        elif raw_line.startswith("##") or raw_line.startswith("#"):
            header_text = clean_text(raw_line.lstrip("#").strip())
            if header_text:
                pdf.ln(5)
                pdf.set_font("Arial", "B", 14)
                pdf.set_text_color(25, 55, 109)
                pdf.multi_cell(0, 8, header_text)
                pdf.ln(1)
        elif raw_line.startswith("---") and len(raw_line) <= 5:
            pdf.ln(2)
            y = pdf.get_y()
            pdf.set_draw_color(180, 180, 180)
            pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
            pdf.ln(4)
        else:
            pdf.set_font("Arial", "", 10)
            pdf.set_text_color(0, 0, 0)
            pdf.multi_cell(0, 6, clean_text(raw_line))

        i += 1

    pdf.set_y(-25)
    pdf.set_font("Arial", "I", 8)
    pdf.set_text_color(120, 120, 120)
    pdf.cell(0, 10, f"Generated on {datetime.now().strftime('%B %d, %Y')}", ln=1, align="C")
    pdf.cell(0, 5, "Powered by TravelMate AI", ln=1, align="C")

    pdf_data = pdf.output(dest="S")
    if isinstance(pdf_data, str):
        pdf_data = pdf_data.encode("latin-1")
    return pdf_data


# ------------------------------------------------------------------
# Helper: Email Validator
# ------------------------------------------------------------------
def is_valid_email(email):
    # Standard email regex pattern
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


# ------------------------------------------------------------------
# Login Page
# ------------------------------------------------------------------
if not st.session_state.logged_in:
    st.title("🌍 TravelMate AI")
    st.markdown("### Welcome! Please log in or register to continue.")

    with st.form("login_form"):
        first_name = st.text_input("First Name", placeholder="e.g. John")
        email_id = st.text_input("Email ID", placeholder="e.g. john@example.com")
        login_btn = st.form_submit_button("Log In / Register")

    if login_btn:
        cleaned_name = first_name.strip()
        cleaned_email = email_id.strip().lower()

        if not cleaned_name or not cleaned_email:
            st.error("Please enter both First Name and Email ID.")
        elif not is_valid_email(cleaned_email):
            st.error("Please enter a valid email address (e.g., name@domain.com).")
        else:
            # Check CSV, login or create new user
            uid, stored_name = get_or_create_user(cleaned_name, cleaned_email)

            # Set Session State
            st.session_state.current_user_id = uid
            st.session_state.preferred_name = stored_name
            st.session_state.email_id = cleaned_email
            st.session_state.greeting_text = get_random_greeting(stored_name)
            st.session_state.logged_in = True

            # Update URL params to persist login
            st.query_params.update({
                "user_id": uid,
                "user_name": stored_name,
                "user_email": cleaned_email
            })

            st.rerun()

    st.stop()

# ------------------------------------------------------------------
# Main App UI
# ------------------------------------------------------------------
st.title("🌍 TravelMate AI Agent")
st.markdown(f"### {st.session_state.greeting_text}")
st.markdown("Plan your trip from start to destination!")

with st.sidebar:
    st.markdown(f"## 👋 {st.session_state.preferred_name}")
    st.caption(f"ID: #{st.session_state.current_user_id} | {st.session_state.email_id}")

    # SHOW USAGE LIMIT IF USER ID >= 100
    try:
        user_id_int = int(st.session_state.current_user_id)
        if user_id_int >= 100:
            remaining = get_remaining_count(st.session_state.current_user_id, st.session_state.email_id)
            if remaining > 0:
                st.success(f"📊 Itineraries left today: **{remaining}/{DAILY_LIMIT}**")
            else:
                st.error(f"🚫 Out of itineraries for today!")
                st.info("Come back tomorrow for 5 more!")
    except:
        pass

    col1, col2 = st.columns(2)
    with col1:
        if st.button("New Chat", key="start_new_chat_btn", use_container_width=True):
            st.session_state.generated_itinerary = None
            st.session_state.generated_pdf_bytes = None
            st.session_state.generated_title = None
            st.session_state.viewing_saved_itinerary = None
            st.session_state.greeting_text = get_random_greeting(st.session_state.preferred_name)
            st.rerun()
    with col2:
        if st.button("Logout", key="logout_btn", use_container_width=True):
            st.query_params.clear()
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()


    st.markdown("---")
    st.header("📍 Trip Details")

    start_address = st.text_input("Starting Address", placeholder="e.g. 123 Main St, New York, NY",
                                  key="start_address_input")
    dest_address = st.text_input("Destination Address", placeholder="e.g. 456 Ocean Dr, Miami, FL",
                                 key="dest_address_input")
    num_people = st.number_input("👥 How many people?", min_value=1, max_value=20, value=1, key="num_people_input")
    start_date = st.date_input("Start Date", value=datetime.now().date(), key="start_date_input")
    days = st.number_input("How many days?", min_value=1, max_value=30, value=3, key="days_input")
    budget = st.selectbox("Budget Level", ["Economy", "Mid-range", "Luxury"], key="budget_input")
    travel_style = st.multiselect("Interests", ["Food", "History", "Adventure", "Relaxation", "Shopping"],
                                  key="travel_style_input")

    st.markdown("---")
    st.markdown("### 💬 Any specific requests?")
    special_requests = st.text_area("Special notes, must-visit places, dietary preferences:",
                                    placeholder="e.g. I'm vegetarian...", key="special_requests_input")

    submit = st.button("Generate Itinerary ✨", key="generate_itinerary_btn")

    if st.session_state.generated_itinerary or st.session_state.viewing_saved_itinerary:
        if st.button("✨ Clear Screen", key="new_search_btn"):
            st.session_state.generated_itinerary = None
            st.session_state.generated_pdf_bytes = None
            st.session_state.generated_title = None
            st.session_state.viewing_saved_itinerary = None
            st.rerun()

    st.markdown("---")
    st.markdown("### 📂 My Previous Itineraries")

    # Fetch ONLY the current logged-in user's itineraries
    saved_records = load_saved_itineraries(st.session_state.current_user_id)

    if saved_records:
        for idx, record in enumerate(saved_records):
            title = record.get("title", "Untitled")
            group_sz = record.get("group_size", "?")
            dates = record.get("dates", "?")
            pdf_path = record.get("pdf_path", "")
            json_path = record.get("json_path", "")

            with st.expander(f"📄 {title}"):
                st.caption(f"👥 {group_sz} | 📅 {dates}")
                c1, c2, c3 = st.columns([1.2, 1.4, 0.8])

                with c1:
                    saved_text = record.get("itinerary_text")
                    if saved_text and st.button("👁️ Show", key=f"show_{idx}"):
                        st.session_state.viewing_saved_itinerary = saved_text
                        st.session_state.generated_itinerary = None
                        st.session_state.generated_pdf_bytes = None
                        st.session_state.generated_title = title
                        st.rerun()
                with c2:
                    if pdf_path and os.path.exists(pdf_path):
                        with open(pdf_path, "rb") as f:
                            pdf_bytes_file = f.read()
                        st.download_button(label="📥 PDF", data=pdf_bytes_file,
                                           file_name=record.get("filename", "itinerary.pdf"), mime="application/pdf",
                                           key=f"saved_pdf_{idx}")
                    else:
                        st.caption("PDF missing")
                with c3:
                    if st.button("🗑️ Del", key=f"del_{idx}"):
                        delete_itinerary_record(pdf_path, json_path)
                        st.rerun()
    else:
        st.info("No past itineraries yet.")

# ------------------------------------------------------------------
# Display Area
# ------------------------------------------------------------------
if st.session_state.viewing_saved_itinerary:
    st.success(f"📂 Showing saved itinerary: {st.session_state.generated_title}")
    st.markdown("---")
    st.markdown(st.session_state.viewing_saved_itinerary)

elif st.session_state.generated_itinerary:
    st.success(st.session_state.generated_title or f"Here is your plan, {st.session_state.preferred_name}!")
    st.markdown("---")
    st.markdown(st.session_state.generated_itinerary)
    st.markdown("---")
    st.download_button(
        label="📥 Download Itinerary as PDF",
        data=st.session_state.generated_pdf_bytes,
        file_name=st.session_state.current_download_filename,
        mime="application/pdf",
        key="current_pdf_dl",
    )

# ------------------------------------------------------------------
# Generation Logic
# ------------------------------------------------------------------
if submit:
    if not start_address or not dest_address:
        st.warning("Please enter both Starting Address and Destination Address!")
    else:
        # CHECK DAILY LIMIT
        user_id_int = int(st.session_state.current_user_id)

        if user_id_int >= 100:  # Apply limit only to users with ID >= 100
            if not is_user_eligible_for_generation(st.session_state.current_user_id, st.session_state.email_id):
                st.error("🚫 You are out of itineraries, come back tomorrow!")
                remaining = get_remaining_count(st.session_state.current_user_id, st.session_state.email_id)
                st.info(f"Daily limit: {DAILY_LIMIT} itineraries. Remaining today: {remaining}")
                st.stop()

        with st.spinner("Planning your trip..."):
            date_list = []
            for i in range(days):
                day_date = start_date + timedelta(days=i)
                date_list.append(day_date.strftime("%A, %B %d, %Y"))

            dates_summary = "\n".join([f"Day {i + 1}: {date_list[i]}" for i in range(days)])
            group_info = f"{num_people} person" if num_people == 1 else f"{num_people} people"

            prompt = f"""
You are a professional AI Travel Agent planning a trip for a GROUP.

TRIP DETAILS:
- Starting from: {start_address}
- Going to: {dest_address}
- GROUP SIZE: {group_info}
- Trip duration: {days} days
- Start date: {start_date.strftime('%b %d, %Y')}
- Budget Level: {budget} (PER PERSON)
- Interests: {', '.join(travel_style) if travel_style else 'General sightseeing'}

TRIP DATES:
{dates_summary}

{"SPECIAL REQUESTS: " + special_requests if special_requests else ""}

IMPORTANT REQUIREMENTS:
1. Plan activities that work for {num_people} {'person' if num_people == 1 else 'people'}
2. Include group activities where multiple people can enjoy together
3. Calculate costs:
   - Per person costs clearly marked
   - Total group costs
   - Total trip cost for the entire group
4. Hotel recommendations must accommodate {num_people} {'person' if num_people == 1 else 'people'}
5. Restaurant recommendations must have capacity for {num_people} {'person' if num_people == 1 else 'people'}
6. Estimated driving distance from {start_address} to {dest_address}
7. Estimated drive time
8. If the drive is long, suggest a halfway point or rest stop

FORMAT YOUR RESPONSE WITH CLEAR SECTIONS:

## ROAD TRIP OVERVIEW

---

## Day 1

---

## Hotel Recommendations

---

## TRIP SUMMARY
"""
            try:
                response = client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=prompt
                )

                itinerary_text = response.text

                pdf_bytes = create_pdf(
                    itinerary_text,
                    start_address,
                    dest_address,
                    num_people,
                    days,
                    start_date.strftime("%b %d, %Y"),
                )

                title = f"✈️ {dest_address[:25]} - {start_date.strftime('%b %d, %Y')}"
                safe_dest = re.sub(r"[^a-zA-Z0-9]+", "_", dest_address[:20]).strip("_")
                if not safe_dest: safe_dest = "destination"
                filename = f"TravelMate_{safe_dest}_{start_date.strftime('%Y%m%d')}.pdf"

                st.session_state.generated_itinerary = itinerary_text
                st.session_state.generated_pdf_bytes = pdf_bytes
                st.session_state.generated_title = title
                st.session_state.current_download_filename = filename
                st.session_state.viewing_saved_itinerary = None

                # Pass user ID to save under their specific folder
                save_itinerary_record(
                    user_id=st.session_state.current_user_id,
                    pdf_bytes=pdf_bytes,
                    itinerary_text=itinerary_text,
                    title=title,
                    group_size=group_info,
                    dates=f"{days} days",
                    dest=dest_address,
                    start_date_str=start_date.strftime("%b %d, %Y"),
                )

                # INCREMENT USAGE COUNT ONLY AFTER SUCCESSFUL GENERATION
                increment_usage_count(st.session_state.email_id, st.session_state.current_user_id)

                st.rerun()

            except Exception as e:
                st.error(f"❌ Error: {str(e)}")
                st.info("💡 Try again in a few moments, or check your API Key limits.")

