FROM python:3.9

RUN mkdir /app && mkdir /out
WORKDIR /app

RUN curl https://stockfishchess.org/files/stockfish_12_linux_x64.zip -o stockfish.zip \
    && unzip stockfish.zip \
    && chmod +x stockfish_20090216_x64 \
    && ln -s /app/stockfish_20090216_x64 /usr/bin/stockfish

RUN pip install python-chess requests

COPY openings.py .

ENTRYPOINT ["python", "openings.py"]
