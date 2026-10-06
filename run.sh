#!/bin/bash
cd "$(dirname "$0")"

if [ ! -f .installed ]
  then
    export DEBIAN_FRONTEND=noninteractive
    apt-get install -qq -y python3.10-venv build-essential libdbus-glib-1-dev libgirepository1.0-dev libcairo2-dev libxt-dev sqlite3
    python3 -m venv viam-env
    viam-env/bin/pip install -q --disable-pip-version-check --upgrade -r requirements.txt
    if [ $? -eq 0 ]
      then
        touch .installed
    fi
fi

source viam-env/bin/activate

# Be sure to use `exec` so that termination signals reach the python process,
# or handle forwarding termination signals manually
exec python3 -m main "$@"
