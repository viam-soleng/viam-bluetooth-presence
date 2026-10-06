#!/bin/bash
cd "$(dirname "$0")"

if [ ! -f .installed ]
  then
    export DEBIAN_FRONTEND=noninteractive
    # PyGObject and dbus-python come from the OS, so pip compiles nothing
    apt-get install -qq -y python3-venv python3-gi python3-dbus &&
      python3 -m venv --system-site-packages viam-env &&
      viam-env/bin/pip install -q --disable-pip-version-check --upgrade -r requirements.txt &&
      touch .installed || exit 1
fi

source viam-env/bin/activate

# Be sure to use `exec` so that termination signals reach the python process,
# or handle forwarding termination signals manually
exec python3 -m main "$@"
