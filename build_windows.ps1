pip install -r requirements.txt
python -m PyInstaller --onefile --windowed --name meter_simulator meter_simulator.py
python -m PyInstaller --onefile --windowed --name dashboard_direct_v2 dashboard.py
Write-Host "Listo: dist\meter_simulator.exe y dist\dashboard_direct_v2.exe"
