pip install -r requirements.txt
python -m PyInstaller --onefile --windowed --name meter_simulator meter_simulator.py
python -m PyInstaller --onefile --windowed --name dashboard dashboard.py
Write-Host "Listo: dist\meter_simulator.exe y dist\dashboard.exe"
