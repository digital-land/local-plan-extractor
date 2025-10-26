# Local Plan Extractor

Extract information from local planning authority websites and local plan documents to seed data for https://planning.data.gov.uk 

The data extracted is in the format defined by the following data specifications:

* [Local plan](https://digital-land.github.io/specification/specification/local-plan/) &mdash; headline information including housing numbers currently found in code local plan documents 
* [Local plan timetable](https://digital-land.github.io/specification/specification/local-plan-timetable/) &mdash; estimated and actual dates for milestones, currently found in [Local Development Scheme](https://digital-land.github.io/specification/specification/local-plan/) documents 

Local planning authorities are encouraged to review and improve this data using using the [Check and Provide](https://provide.planning.data.gov.uk) service.

# Set-up

We recommend working in [virtual environment](http://docs.python-guide.org/en/latest/dev/virtualenvs/) before installing the python [requirements](requirements.txt), [makerules](https://github.com/digital-land/makerules) and other dependencies. Requires Python 3.12 or newer, and Make v4.0 or above.

    $ make init

The build needs an [ANTHROPIC_API_KEY](https://docs.claude.com/en/docs/get-started) for Claude:

    $ export ANTHROPIC_API_KEY='your-key-here'

# Building the data

    $ make

# Licence

The software in this project is open source and covered by the [LICENSE](LICENSE) file.

Individual datasets copied into this repository may have specific copyright and licensing, otherwise all content and data in this repository is
[© Crown copyright](http://www.nationalarchives.gov.uk/information-management/re-using-public-sector-information/copyright-and-re-use/crown-copyright/)
and available under the terms of the [Open Government 3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/) licence.
