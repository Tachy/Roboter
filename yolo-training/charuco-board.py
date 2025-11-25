import cv2
import cv2.aruco as aruco
from reportlab.pdfgen import canvas  # type: ignore
from reportlab.lib.units import mm  # type: ignore

# Board-Parameter
squaresX, squaresY = 10, 15
squareLength_mm, markerLength_mm = 50, 35
dpi = 300

# ArUco-Dictionary
dict_aruco = aruco.getPredefinedDictionary(aruco.DICT_5X5_1000)

# Charuco-Board (alte API)
board = aruco.CharucoBoard(
    (squaresX, squaresY), squareLength_mm, markerLength_mm, dict_aruco
)

# Größenberechnung
mm_per_inch = 25.4
W_mm = squaresX * squareLength_mm
H_mm = squaresY * squareLength_mm
W_px = int(W_mm / mm_per_inch * dpi)
H_px = int(H_mm / mm_per_inch * dpi)

# Bild erzeugen
img = board.generateImage((W_px, H_px))

# PNG speichern
png_path = "charuco_50x75.png"
cv2.imwrite(png_path, img)

# PDF maßstabsgetreu speichern
pdf_path = "charuco_50x75.pdf"
c = canvas.Canvas(pdf_path, pagesize=(W_mm * mm, H_mm * mm))
c.drawImage(png_path, 0, 0, width=W_mm * mm, height=H_mm * mm)
c.showPage()
c.save()

print(f"PDF gespeichert unter: {pdf_path}")
