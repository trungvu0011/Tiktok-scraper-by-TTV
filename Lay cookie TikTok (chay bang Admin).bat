@echo off
chcp 65001 >nul
title Lay cookie TikTok
echo ============================================================
echo   TRICH XUAT COOKIE TIKTOK TU CHROME / EDGE
echo ============================================================
echo.
echo  Yeu cau: ban da DANG NHAP tiktok.com tren Chrome hoac Edge.
echo  (Nen DONG het cua so Chrome/Edge truoc khi chay cho chac.)
echo.
echo  Neu cua so nay KHONG chay bang quyen Admin, hay dong lai
echo  roi chuot phai vao file -^> "Run as administrator".
echo.
pause

"C:\Users\Gram\AppData\Local\Programs\Python\Python312\python.exe" "%~dp0Code\Tiktok-scraper-main\extract_cookies.py"

echo.
echo ============================================================
echo  Xong. Xem ket qua o tren va file tiktok_state.json.
echo ============================================================
echo.
pause
