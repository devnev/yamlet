SHELL:=/bin/bash -ex

.PHONY: test
test:
	for golden in golden/*; do \
	  diff -u <(./yamlet.py test/$${golden##*/}) $${golden}; \
	done
