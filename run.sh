#!/bin/bash
cd `dirname $0`

version_compare() {
  if [[ $1 == $2 ]]
  then
      return 0
  fi
  local IFS=.
  local i ver1=($1) ver2=($2)
  for ((i=${#ver1[@]}; i<${#ver2[@]}; i++))
  do
      ver1[i]=0
  done
  for ((i=0; i<${#ver1[@]}; i++))
  do
      if [[ -z ${ver2[i]} ]]
      then
          ver2[i]=0
      fi
      if ((10#${ver1[i]} > 10#${ver2[i]}))
      then
          return 1
      fi
      if ((10#${ver1[i]} < 10#${ver2[i]}))
      then
          return 2
      fi
  done
  return 0
}

if [ -f .installed ]
  then
    source viam-env/bin/activate
  else
    apt-get install python3-pip -y
    # Get pip version
    pip_version=$(python3 -m pip --version | awk '{print $2}')

    echo "Detected pip version: $pip_version"

    # Base command
    base_command="python3 -m pip install --user virtualenv"

    # Check if pip version is 23.0 or higher
    if version_compare "$pip_version" "23.0"; then
        if [[ $? -eq 1 ]] || [[ $? -eq 0 ]]; then
            base_command="$base_command --break-system-packages"
        fi
    fi

    $base_command
    
    apt install python3.10-venv -y
    python3 -m venv viam-env
    source viam-env/bin/activate
    apt install build-essential libdbus-glib-1-dev libgirepository1.0-dev libcairo2-dev libxt-dev libgirepository1.0-dev -y
    pip3 install --upgrade -r requirements.txt
    if [ $? -eq 0 ]
      then
        touch .installed
    fi
fi

# Be sure to use `exec` so that termination signals reach the python process,
# or handle forwarding termination signals manually
exec python3 -m main $@
