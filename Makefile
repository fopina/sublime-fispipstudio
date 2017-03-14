ifeq ($(OS),Windows_NT)
    PLUGINDIR=hello
else
    UNAME_S := $(shell uname -s)
    ifeq ($(UNAME_S),Linux)
        PLUGINDIR=${HOME}/.config/sublime-text-3/Packages/
    endif
    ifeq ($(UNAME_S),Darwin)
        PLUGINDIR=${HOME}/Library/Application\ Support/Sublime\ Text\ 3/Packages/
    endif
endif
PACKAGE_NAME=FISPIP\ Studio

install:
	mkdir -p ${PLUGINDIR}${PACKAGE_NAME}
	cp -a * ${PLUGINDIR}${PACKAGE_NAME}/
import:
	cp -a ${PLUGINDIR}${PACKAGE_NAME}/* .
