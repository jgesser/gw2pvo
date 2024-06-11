FROM python:3.7

RUN git clone https://github.com/jgesser/gw2pvo /app
WORKDIR /app

RUN apt-get update && apt-get install -y pandoc
RUN make README.rst

RUN pip install .
RUN python setup.py install

ENTRYPOINT exec gw2pvo --config /gw2pvo.cfg

