run:
	uvicorn app.main:app --reload --port 8000

test:
	pytest -q
