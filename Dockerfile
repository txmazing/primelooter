FROM mcr.microsoft.com/playwright/python:next

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt
RUN python -m playwright install

COPY primelooter.py primelooter.py
CMD [ "python", "primelooter.py" , "--loop" ]