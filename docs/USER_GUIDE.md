# SahajMails – User Guide  
### Send Personalized Bulk Emails Easily
**No coding. No server. Just upload, type, send.**

---

## 1. Upload Your Contact List  
**File:** CSV or Excel (.xlsx, .xls)  
**Required column:** `email`  
**Optional columns:** `firstName`, `company`, `role`, etc.

**Example CSV:**
```csv
email,firstName,company
alice@example.com,Alice,Acme Corp
bob@work.com,Bob,StartupXYZ
```

> **Tip:** Save Excel files as **UTF-8 CSV** to avoid encoding issues.

---

## 2. Set Up Gmail App Password (One-Time)  

1. Go to [myaccount.google.com/security](https://myaccount.google.com/security)  
2. Turn on **2-Step Verification** (if not already)  
3. Search **"App Passwords"**  
4. Select **App name** → **Generate**  
5. Copy the **16-character password** (e.g., `abcd efgh ijkl mnop`)  

> **Never use your real Gmail password.** App Password keeps you secure.

---

## 3. Compose Your Email  

- **Subject:** Keep it short and clear  
- **Body:** Use **Markdown** or **HTML**  
- **Placeholders:** Type `{{ columnName }}` to insert data  

**Examples:**
```markdown
Dear {{ firstName }},

Welcome to {{ company }}! We're excited to have you.

Best regards,  
Your Team
```

> **Spaces & case ignored:** `{{ FirstName }}`, `{{firstName}}`, `{{ firstName }}` → all work!

---

## 4. Use Placeholders Easily  

After uploading your file, you’ll see **click-to-copy buttons** like:

`{{ firstName }}` `{{ company }}` `{{ email }}`

Click any → automatically copied to clipboard!

---

## 5. Attach Files (Optional)  

- PDFs, images, docs — all supported  
- Drag & drop multiple files  
- Attachments are sent to **every recipient**

---

## 6. Preview & Test  

1. **Live Preview** updates as you type  
2. Click **"Send Test to Myself"**  
3. Check your inbox (including spam)  

> Always test before bulk sending!

---

## 7. Send to All Contacts  

1. Click **"Start Bulk Send"**  
2. Watch the **progress bar** and **real-time log**  
3. Each email is sent with a **2-second delay** (Gmail-safe)  

**Done!** Balloons appear when complete.

---

## Troubleshooting  

| Issue | Solution |
|------|----------|
| `Authentication failed` | Double-check **App Password** and **2FA enabled** |
| `email column missing` | Rename column to exactly `email` |
| Emails go to spam | Use professional subject, avoid ALL CAPS, add unsubscribe link |
| File not loading | Save as **UTF-8 CSV**, avoid special characters |

---

## Safety & Privacy  

- Runs **100% on your device**  
- **No data stored** or sent anywhere  
- **No login to third-party servers**  
- Open-source (MIT) – [github.com/sahajrajmalla/sahajmails](https://github.com/sahajrajmalla/sahajmails)

---

**Made with ❤️ by Sahaj Raj Malla**  
*Free • Secure • Simple*
