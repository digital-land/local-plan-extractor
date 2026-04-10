# generated static site pages
DOCS_DIR=docs/

# downloaded data from other sources
CACHE_DIR=var/cache/

# generated CSV datasets
DATASET_DIR=dataset/

SOURCE_DATA=\
	$(CACHE_DIR)organisation.csv\
	$(CACHE_DIR)local-authority-district.csv\
	$(CACHE_DIR)local-planning-authority.csv\
	$(CACHE_DIR)local-planning-authority.geojson\
	$(CACHE_DIR)local-plan-document-type.csv\
	$(CACHE_DIR)local-planning-authority-lookup.csv

CORE_DOCUMENTS=$(wildcard document/*.pdf)

TARGETS=$(patsubst document/%,local-plan/%,$(patsubst %.pdf,%.json,$(CORE_DOCUMENTS)))

.PHONY: all init server test black scrape download download-all extract-dates generate-config clobber clean

all::	$(SOURCE_DATA) $(TARGETS)
	python3 bin/generate-csvs.py --output-dir $(DATASET_DIR)
	python3 bin/render.py

source/%.json:
	@mkdir -p $(dir $@)
	python3 bin/find-local-plan.py $(basename $(notdir $@)) > $@

local-plan/%.json: document/%.pdf
	@mkdir -p $(dir $@)
	python3 bin/local-plan-extractor.py $? > $@

$(CACHE_DIR)prototype.csv:
	@mkdir -p $(CACHE_DIR)
	curl -qfsL 'https://local-plans.prototype.planning.data.gov.uk/local-plans/local-plan-data.csv' > $@

$(CACHE_DIR)organisation.csv:
	@mkdir -p $(CACHE_DIR)
	curl -qfsL "https://files.planning.data.gov.uk/organisation-collection/dataset/organisation.csv" > $@

$(CACHE_DIR)%.csv:
	@mkdir -p $(dir $@)
	curl -qfsL 'https://files.planning.data.gov.uk/dataset/$(notdir $@)' > $@

$(CACHE_DIR)%.geojson:
	@mkdir -p $(dir $@)
	curl -qfsL 'https://files.planning.data.gov.uk/dataset/$(notdir $@)' > $@


init::
	pip3 install -r requirements.txt

# Run the test suite
test::
	python3 -m unittest discover -s tests -v

# Run find-local-plan.py across all authorities (hits Claude API + web — slow)
scrape::
	python3 bin/run-all-authorities.py

# Download the main adopted local plan PDF per authority (default mode)
download::
	python3 bin/download-documents.py

# Download all document URLs across all plans
download-all::
	python3 bin/download-documents.py --all

# Extract adoption dates from local plans using Claude (hits Claude API — slow)
extract-dates::
	python3 bin/extract-adoption-dates.py

# Generate config files for local plans
generate-config::
	python3 bin/generate-local-plan-config-files.py

server::
	python3 -m http.server -d $(DOCS_DIR)

black::
	black bin

clobber::
	rm -f $(TARGETS)
	rm -f $(DATASET_DIR)local-plan.csv $(DATASET_DIR)local-plan-document.csv

clean::
	rm -rf var/
