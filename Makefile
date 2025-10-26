# downloaded data from other sources
CACHE_DIR=var/cache/

SOURCE_DATA=\
     $(CACHE_DIR)organisation.csv\
	 $(CACHE_DIR)local-planning-authority.csv


CORE_DOCUMENTS=$(wildcard document/*.pdf)

TARGETS=$(patsubst document/%,local-plan/%,$(patsubst %.pdf,%.json,$(CORE_DOCUMENTS)))

all::	$(SOURCE_DATA) $(TARGETS)

local-plan/%.json: document/%.pdf
	@mkdir -p $(dir $@)
	python3 bin/local-plan-extractor.py $? > $@

$(CACHE_DIR)prototype.csv:
	@mkdir -p $(CACHE_DIR)
	curl -qfsL 'https://local-plans.prototype.planning.data.gov.uk/local-plans/local-plan-data.csv' > $@

$(CACHE_DIR)organisation.csv:
	@mkdir -p $(CACHE_DIR)
	curl -qfsL "https://files.planning.data.gov.uk/organisation-collection/dataset/organisation.csv" > $@

$(CACHE_DIR)local-planning-authority.csv:
	@mkdir -p $(CACHE_DIR)
	curl -qfsL 'https://files.planning.data.gov.uk/dataset/local-planning-authority.csv' > $@
            

init::
	pip3 install -r requirements.txt

clobber::
	rm -f $(TARGETS)

clean::
	rm -rf var/
