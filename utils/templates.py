from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

env = Environment(
    loader=FileSystemLoader("templates"),
    cache_size=0,
    auto_reload=True,
)
templates = Jinja2Templates(directory="templates")
templates.env = env
