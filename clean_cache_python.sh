# Makefile
# =====================================================
# Komfort-Befehle für ORÓMA-Entwicklung
# =====================================================

# Temporäre Dateien entfernen
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +