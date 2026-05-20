cd /opt/ai/oroma
source venv/bin/activate

# eigene Test-DB
export OROMA_DB_SIM="/opt/ai/oroma/data/oroma_sim.db"

# Simulation starten
python tools/sim_learn.py --day 5000