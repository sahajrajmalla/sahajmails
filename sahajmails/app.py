# sahajmails/app.py
"""
SahajMails – Simple Bulk Email Sender (Open-Source)
A simple, secure, and user-friendly Streamlit application that enables anyone to send
personalized bulk emails using Gmail's SMTP server. Designed for layman users.
"""
from __future__ import annotations
import re
import time
import logging
from typing import List, Tuple, Dict, Any, Optional
import streamlit as st
import pandas as pd
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage
import markdown

# Configuration & Constants
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

BULK_DELAY_SECONDS: float = 2.0
COPY_ICON = "📋"


# Core Email Logic
def connect_smtp(gmail_address: str, app_password: str) -> smtplib.SMTP:
    server = smtplib.SMTP("smtp.gmail.com", 587, timeout=30)
    server.starttls()
    server.login(gmail_address, app_password)
    logger.info("SMTP connection established.")
    return server

def build_personalized_body(template: str, row_data: Dict[str, Any], columns: List[str]) -> str:
    personalized = template
    for col in columns:
        pattern = re.compile(rf"{{\{{\s*{re.escape(col)}\s*\}}}}", re.IGNORECASE)
        value = str(row_data.get(col, "")) if pd.notna(row_data.get(col)) else ""
        personalized = pattern.sub(value, personalized)
    return personalized

def create_mime_message(
    sender: str, recipient: str, subject: str, html_body: str,
    attachments: List[Tuple[str, bytes, Optional[str]]]
) -> MIMEMultipart:
    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html"))
    for filename, data, mime_type in attachments:
        part = MIMEImage(data, name=filename) if mime_type and "image" in mime_type.lower() else MIMEApplication(data, name=filename)
        part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
        msg.attach(part)
    return msg

def render_email_body(body: str) -> str:
    stripped = body.strip()
    inner_html = stripped if stripped.lower().startswith(("<!doctype html>", "<html")) else markdown.markdown(stripped)
    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8">
      <style>
        body {{ background:#ffffff !important; color:#000000 !important; margin:1.5rem; font-family:Arial,Helvetica,sans-serif; line-height:1.6; }}
        h1,h2,h3 {{ color:#1f77b4; }}
        a {{ color:#1f77b4; }}
      </style>
    </head>
    <body>{inner_html}</body>
    </html>
    """

def generate_dummy_row(columns: List[str]) -> Dict[str, str]:
    return {col: f"[Example {col.replace('_', ' ').title()}]" for col in columns}


# Streamlit App (Polished UI/UX)
def main() -> None:
    st.set_page_config(page_title="SahajMails", page_icon="📧", layout="centered")
    
    # Header
    st.markdown(
        """
        <div style="text-align: center; padding: 1.5rem 0;">
            <h1 style="color: #1f77b4; margin:0;">📧 SahajMails</h1>
            <p style="color:#555; font-size:1.1rem; margin:0.5rem 0 0;">
                Send <strong>personalized bulk emails</strong> — Free, Secure, Simple
            </p>
        </div>
        """, unsafe_allow_html=True
    )

    # Step 0: Guide
    st.info(
        """
        **Gmail App Password (Required)**  
        1. Go to [myaccount.google.com/security](https://myaccount.google.com/security)  
        2. Enable **2-Step Verification** (if not already)
        3. Search **"App Passwords"** → Select **App name** → Generate  
        4. Copy the **16-character password** and paste below in App Password field
        """
    )

    # Step 1: Upload
    st.markdown("---")
    st.subheader("1. Upload Contact List (CSV/Excel)")
    uploaded_file = st.file_uploader(
        "", type=["csv", "xlsx", "xls"],
        help="Must have an `email` column. Example: email, firstName, company"
    )

    df: Optional[pd.DataFrame] = None
    if uploaded_file:
        try:
            ext = uploaded_file.name.rsplit(".", 1)[-1].lower()
            df = pd.read_csv(uploaded_file) if ext == "csv" else pd.read_excel(uploaded_file)
            if "email" not in df.columns:
                st.error("❌ Column **`email`** is missing.")
                df = None
            else:
                st.success(f"✅ Loaded **{len(df):,} contacts**")
                with st.expander("👁️ Preview first 10 rows"):
                    st.dataframe(df.head(10))
        except Exception as e:
            st.error(f"❌ File error: {e}")
            logger.exception("Upload failed")

    if df is not None and not df.empty:
        columns = df.columns.tolist()

        # Step 2: Credentials
        st.markdown("---")
        st.subheader("2. Gmail Login")
        c1, c2 = st.columns(2)
        with c1:
            sender_email = st.text_input("Your Gmail", placeholder="you@gmail.com")
        with c2:
            app_password = st.text_input("App Password", type="password", help="16 chars from Google")

        # Step 3: Compose
        st.markdown("---")
        st.subheader("3. Compose Email")
        subject = st.text_input("Subject", placeholder="e.g., Welcome to Our Community!")
        message_body = st.text_area(
            "Body (Markdown or HTML)", height=300,
            placeholder="Dear {{ firstName }},\n\nThank you for joining...\n\nBest,\nYour Team",
            help="Use placeholders like {{ firstName }}, {{ company }} — spaces ignored"
        )

        # Placeholder Helper
        if columns:
            st.markdown("**Available Placeholders:**")
            cols = st.columns(4)
            for i, col in enumerate(columns):
                with cols[i % 4]:
                    if st.button(f"`{{{{ {col} }}}}`", key=f"_{col}"):
                        st.code(f"{{{{ {col} }}}}")


        # Placeholder Guide
        st.caption(
            """
            **Placeholder Tips:**  
            • Use `{{ columnName }}` — matches your column name (case/spaces ignored)  
            • Example: `{{ firstName }}`, `{{ Company }}`, `{{ email }}`  
            • Click any above to copy
            """
        )

        # Step 4: Attachments
        st.markdown("---")
        st.subheader("4. Attachments (Optional)")
        uploaded_attachments = st.file_uploader("", accept_multiple_files=True, label_visibility="collapsed")
        attachment_data: List[Tuple[str, bytes, Optional[str]]] = [
            (f.name, f.read(), f.type) for f in uploaded_attachments
        ] if uploaded_attachments else []
        if attachment_data:
            st.caption(f"📎 {len(attachment_data)} file(s) attached")

        # Step 5: Preview
        st.markdown("---")
        st.subheader("5. Live Preview")
        if message_body.strip():
            preview_row = df.iloc[0].to_dict()
            html = render_email_body(build_personalized_body(message_body, preview_row, columns))
            st.components.v1.html(html, height=600, scrolling=True)
        else:
            st.info("Type your message to see preview")

        # Step 6: Test
        st.markdown("---")
        st.subheader("6. Send Test Email")
        if st.button("Send Test to Myself", type="secondary", use_container_width=True):
            if not all([sender_email, app_password, subject, message_body.strip()]):
                st.error("Fill all fields first.")
            else:
                with st.spinner("Sending test..."):
                    try:
                        server = connect_smtp(sender_email, app_password)
                        dummy = generate_dummy_row(columns)
                        html_body = render_email_body(build_personalized_body(message_body, dummy, columns))
                        msg = create_mime_message(sender_email, sender_email, subject, html_body, attachment_data)
                        server.sendmail(sender_email, sender_email, msg.as_string())
                        server.quit()
                        st.success(f"Test sent to **{sender_email}**!")
                        st.balloons()
                    except smtplib.SMTPAuthenticationError:
                        st.error("Invalid App Password or 2FA not enabled.")
                    except Exception as e:
                        st.error(f"Failed: {e}")

        # Step 7: Bulk Send
        st.markdown("---")
        st.subheader("7. Send to All Contacts")
        if st.button("Start Bulk Send", type="primary", use_container_width=True):
            if not all([sender_email, app_password, subject, message_body.strip()]):
                st.error("All fields required.")
            else:
                progress = st.progress(0)
                log = st.empty()
                logs: List[str] = []
                try:
                    server = connect_smtp(sender_email, app_password)
                    for i, row in df.iterrows():
                        rec = row["email"]
                        try:
                            html = render_email_body(build_personalized_body(message_body, row.to_dict(), columns))
                            msg = create_mime_message(sender_email, rec, subject, html, attachment_data)
                            server.sendmail(sender_email, rec, msg.as_string())
                            logs.append(f"✅ Sent to **{rec}** \n")
                            time.sleep(BULK_DELAY_SECONDS)
                        except Exception as e:
                            logs.append(f"❌ **{rec}**: {e}")
                            logger.warning(f"Failed {rec}: {e}")
                        log.markdown("\n".join(logs), unsafe_allow_html=True)
                        progress.progress((i + 1) / len(df))
                    server.quit()
                    st.success(f"Bulk send complete: **{len(df)} emails** sent.")
                    st.balloons()
                except Exception as e:
                    st.error(f"Bulk failed: {e}")
                    logger.exception("Bulk error")
    else:
        st.info("↑ Upload a CSV/Excel file to begin")

    # Footer
    st.markdown("---")
    st.markdown(
        """
        <div style="text-align:center; color:#777; font-size:0.9rem; padding:1rem;">
            Made with ❤️ by <strong>Sahaj Raj Malla</strong> •
            <a href="https://github.com/sahajrajmalla/sahajmails" target="_blank">GitHub</a> • MIT License
        </div>
        """, unsafe_allow_html=True
    )

if __name__ == "__main__":
    main()