# Python module: nothing to compile. module.tar.gz bundles the source,
# and run.sh builds the venv on the device.
MODULE_FILES := meta.json main.py run.sh requirements.txt src

.PHONY: module.tar.gz reload upload clean

module.tar.gz:
	tar czf module.tar.gz --exclude=__pycache__ $(MODULE_FILES)

reload:
	$(if $(PART_ID),,$(error usage: make reload PART_ID=<part id>))
	viam module reload-local --part-id=$(PART_ID)

upload: module.tar.gz
	$(if $(VERSION),,$(error usage: make upload VERSION=<version>))
	viam module upload --version=$(VERSION) --platform=linux/arm64 --upload=module.tar.gz
	viam module upload --version=$(VERSION) --platform=linux/amd64 --upload=module.tar.gz

clean:
	rm -f module.tar.gz
	rm -rf __pycache__ src/__pycache__
