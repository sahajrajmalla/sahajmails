<div align="center">

# SahajMails

**Send personalized bulk emails — Free, Secure, Simple**

No servers. No coding. Just upload, type, send.

[![PyPI version](https://badge.fury.io/py/sahajmails.svg)](https://badge.fury.io/py/sahajmails)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

</div>

---

## Features

- **100% Local**: Your data never leaves your device  
- **Gmail App Password**: Secure authentication (no real password used)  
- **CSV/Excel Upload**: Supports `.csv`, `.xlsx`, `.xls`  
- **Smart Placeholders**: `{{ firstName }}` — case and space tolerant  
- **Live Preview**: Real-time HTML rendering  
- **Test Email**: Send to yourself before bulk  
- **Attachments**: PDFs, images, documents  
- **Progress Tracking**: Real-time log and progress bar  
- **Markdown & HTML**: Full email formatting support  
- **Gmail-Safe**: 2-second delay between sends  

---

## Quick Start

```bash
pip install sahajmails
sahajmails
```

Open [http://localhost:8501](http://localhost:8501)

---

## 7-Step Guide

1. **Upload** your contact list (must have `email` column)  
2. **Enter** your Gmail and **App Password**  
3. **Compose** email using `{{ columnName }}` placeholders  
4. **Click** placeholder buttons to copy instantly  
5. **Attach** files (optional)  
6. **Preview** → **Send Test Email**  
7. **Start Bulk Send** → Watch progress

**Example CSV:**
```csv
email,firstName,company
alice@example.com,Alice,Acme Corp
bob@work.com,Bob,StartupXYZ
```

---

## Gmail App Password (Required)

1. Go to [myaccount.google.com/security](https://myaccount.google.com/security)  
2. Enable **2-Step Verification**  
3. Search **"App Passwords"**  
4. Select **App name** → **Generate**  
5. Copy the **16-character password** → paste in the app


---

## Installation

```bash
pip install sahajmails
```

Or from source:

```bash
git clone https://github.com/sahajrajmalla/sahajmails.git
cd sahajmails
pip install -e .
```

---

## Development

```bash
# Install dev dependencies
pip install -e .[dev]

# Run tests
pytest tests/

# Build package
python -m build
```

---

## Contributing

Contributions are welcome!  

1. Fork the repo  
2. Create a branch: `git checkout -b feature/your-idea`  
3. Commit changes: `git commit -m "feat: add X"`  
4. Push and open a Pull Request  

Please follow PEP 8 and include tests.

---

## Security & Privacy

- Runs **entirely on your machine**  
- **No data is stored or transmitted**  
- **Open source** under MIT License  
- Uses **Gmail App Passwords** only  

---

## License

[MIT License](LICENSE) – Free for personal and commercial use.

---

<div align="center">

Made with ❤️ by [Sahaj Raj Malla](https://github.com/sahajrajmalla)  


</div>
