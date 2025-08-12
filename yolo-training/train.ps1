<# ===================================================================
  train.ps1 — Windows 11 YOLOv8m Training @ 1280x720
  - Erstellt/benutzt venv im Projektordner
  - Installiert Ultralytics
  - Legt dataset-Struktur & dataset.yaml an (falls fehlt)
  - Trainiert mit YOLOv8m, imgsz=1280, rect=True
=================================================================== #>

param(
  [int]$epochs = 60,
  [int]$batch  = 8,
  [int]$imgsz  = 1280,
  [string]$model = "yolov8m.pt",
  [string]$projectName = "runs",
  [string]$runName = "train"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# --- Pfade ---
$Root = Split-Path -Path $MyInvocation.MyCommand.Path -Parent
Set-Location $Root
$VenvDir = Join-Path $Root ".venv"
$PyExe   = Join-Path $VenvDir "Scripts\python.exe"
$PipExe  = "$PyExe -m pip"
$YoloExe = Join-Path $VenvDir "Scripts\yolo.exe"

# --- venv anlegen (falls fehlt) ---
# if (-not (Test-Path $PyExe)) {
#   Write-Host ">> Creating venv at $VenvDir"
#   python -m venv $VenvDir
# }

# --- Pakete installieren/aktualisieren ---
# Write-Host ">> Upgrading pip"
# & $PyExe -m pip install --upgrade pip

# Write-Host ">> Installing dependencies (ultralytics, opencv-python)"
# & $PyExe -m pip install --upgrade ultralytics opencv-python

# --- dataset-Struktur sicherstellen ---
$ds = Join-Path $Root "dataset"
$paths = @(
  (Join-Path $ds "images\train"),
  (Join-Path $ds "images\val"),
  (Join-Path $ds "labels\train"),
  (Join-Path $ds "labels\val")
)
foreach ($p in $paths) { if (-not (Test-Path $p)) { New-Item -ItemType Directory -Force -Path $p | Out-Null } }

# --- dataset.yaml erstellen, wenn nicht vorhanden ---
$datasetYaml = Join-Path $Root "dataset.yaml"
if (-not (Test-Path $datasetYaml)) {
  @"
# Pfade relativ zum Projekt
path: dataset
train: images/train
val: images/val

# Klassen anpassen:
names:
  0: unkraut
"@ | Out-File -FilePath $datasetYaml -Encoding UTF8 -Force
  Write-Host ">> Created dataset.yaml template"
}

# --- YOLO CLI bestimmen ---
$yoloCmd = $null
if (Test-Path $YoloExe) {
  $yoloCmd = $YoloExe
} else {
  # Fallback: Modulstart (sollte selten nötig sein)
  $yoloCmd = "$PyExe -m ultralytics"
}

# --- Training starten (1280x720 via imgsz, rechteckige Batches) ---
Write-Host ">> Starting training: model=$model, imgsz=$imgsz, epochs=$epochs, batch=$batch"
$trainArgs = @(
  "task=detect",
  "mode=train",
  "data=`"$datasetYaml`"",
  "model=$model",
  "imgsz=$imgsz",
  "epochs=$epochs",
  "batch=$batch",
  "rect=True",
  "workers=0",
  "project=`"$projectName`"",
  "name=`"$runName`""
)

# Aufruf (beide Varianten unterstützen dieselben Argumente)
if ($yoloCmd -like "*ultralytics") {
  & $PyExe -m ultralytics $trainArgs
} else {
  & $yoloCmd $trainArgs
}

# --- Ergebnis ausgeben ---
$bestPt = Join-Path $Root "$projectName\detect\$runName\weights\best.pt"
if (-not (Test-Path $bestPt)) {
  # Fallback: falls Ultralytics einen anderen Ordnernamen gewählt hat (z.B. train2)
  $candidates = Get-ChildItem -Path (Join-Path $Root "$projectName\detect") -Directory -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending
  foreach ($cand in $candidates) {
    $maybe = Join-Path $cand.FullName "weights\best.pt"
    if (Test-Path $maybe) { $bestPt = $maybe; break }
  }
}

Write-Host "============================================================"
Write-Host " Training finished."
Write-Host " best.pt: $bestPt"
Write-Host "============================================================"

# --- Optional: kurzer Predict-Test auf einem Bild/Ordner (auskommentieren, wenn gewünscht) ---
# $source = "path\to\image_or_folder"
# $predArgs = @("task=detect","mode=predict","model=`"$bestPt`"","source=`"$source`"","imgsz=$imgsz","rect=True")
# if ($yoloCmd -like "*ultralytics") { & $PyExe -m ultralytics $predArgs } else { & $yoloCmd $predArgs }
