#!/bin/bash
set -e
cd "$(dirname "$0")"

# Function to compare version numbers
version_compare() {
  if [[ $1 == $2 ]] 
  then
      return 0
  fi
  local IFS=.
  local i ver1=($1) ver2=($2)
  for (( i=${#ver1[@]}; i<${#ver2[@]}; i++ )) 
  do
      ver1[i]=0
  done
  for (( i=0; i<${#ver1[@]}; i++ ))
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

if [ -f .installed ]; then
    source viam-env/bin/activate
else
    # Update and install required apt packages
    apt-get update
    apt-get install -y \
      python3-pip \
      python3.10-venv \
      python3.10-dev \
      build-essential \
      libffi-dev \
      libdbus-glib-1-dev \
      libgirepository1.0-dev \
      libcairo2-dev \
      libxt-dev \
      sqlite3 \
      meson \
      ninja-build \
      cmake

    # Create symlink so that pkg-config finds "girepository-2.0.pc"
    if [ ! -f /usr/lib/aarch64-linux-gnu/pkgconfig/girepository-2.0.pc ]; then
        ln -s /usr/lib/aarch64-linux-gnu/pkgconfig/gobject-introspection-1.0.pc \
              /usr/lib/aarch64-linux-gnu/pkgconfig/girepository-2.0.pc
    fi

    # Export environment variables to help pkg-config and PyGObject find the necessary files
    export PKG_CONFIG_PATH=/usr/lib/aarch64-linux-gnu/pkgconfig:$PKG_CONFIG_PATH
    export GI_TYPELIB_PATH=/usr/lib/aarch64-linux-gnu/girepository-1.0:$GI_TYPELIB_PATH

    # Get pip version
    pip_version=$(python3 -m pip --version | awk '{print $2}')
    echo "Detected pip version: $pip_version"
    base_command="python3 -m pip install --user virtualenv"
    if version_compare "$pip_version" "23.0" 
    then
        if [[ $? -eq 1 ]] || [[ $? -eq 0 ]]
        then
            base_command="$base_command --break-system-packages"
        fi
    fi

    $base_command

    # Create and activate the virtual environment
    python3 -m venv viam-env
    source viam-env/bin/activate
    pip3 install --upgrade pip

    # Force reinstall PyGObject at a version compatible with system
    pip3 install --prefer-binary --force-reinstall "PyGObject==3.42.1"

    # Install the remaining Python dependencies
    pip3 install --prefer-binary -r requirements.txt

    # Mark installation as successful
    touch .installed
fi

# Execute the main module
exec python3 -m main "$@"