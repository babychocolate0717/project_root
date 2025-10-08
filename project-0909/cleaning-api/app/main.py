from fastapi import FastAPI
from app.schemas import RawEnergyData
from app.cleaning import clean_energy_data

app = FastAPI()

@app.post("/clean")
def clean_endpoint(data: RawEnergyData):
    cleaned = clean_energy_data(data.dict())
    return {"cleaned_data": cleaned}