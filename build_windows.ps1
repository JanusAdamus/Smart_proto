pip install -r requirements.txt
python -m PyInstaller --onefile --windowed --name meter_simulator meter_simulator.py
python -m PyInstaller --onefile --windowed --name dashboard_plug_and_play_v3 dashboard.py
Write-Host "Listo: dist\meter_simulator.exe y dist\dashboard_plug_and_play_v3.exe"
