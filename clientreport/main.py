"""Production entry point: uvicorn clientreport.main:app"""

from clientreport.app import create_app
from clientreport.config import load_settings

app = create_app(load_settings())
