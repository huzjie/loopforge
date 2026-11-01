.PHONY: all gen test bench doctor clean

PY ?= python

all: test bench

test:
	$(PY) -m unittest discover -s tests -t .

bench:
	PYTHONPATH=. $(PY) -m loopforge.cli bench

doctor:
	PYTHONPATH=. $(PY) -m loopforge.cli doctor

clean:
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	find . -name '*.pyc' -delete
