CORE_DOCUMENTS=$(wildcard document/*.pdf)

TARGETS=$(patsubst document/%,local-plan/%,$(patsubst %.pdf,%.json,$(CORE_DOCUMENTS)))

all::	$(TARGETS)

local-plan/%.json: document/%.pdf
	@mkdir -p $(dir $@)
	python3 bin/local-plan-extractor.py $? > $@

init::
	pip3 install -r requirements.txt

clobber::
	rm -f $(TARGETS)

clean::
	rm -rf var/
