from fastapi import FastAPI
# Assuming you have a function called get_eta in surat_brts_eta_fast.py
from surat_brts_eta_fast import get_eta 

app = FastAPI()

@app.get("/")
def read_root():
    return {"status": "Sitilink API is running!"}

@app.get("/api/eta/{stop_id}")
def fetch_bus_eta(stop_id: str):
    # This calls your exact logic!
    eta_data = get_eta(stop_id) 
    
    # Return it as a clean JSON dictionary
    return {
        "stop_id": stop_id,
        "eta_list": eta_data
    }