#!/bin/bash

set -e

docker build . -t chess-exp:latest

mkdir -p out

what=$1

if [[ -z "$what" ]]
then
  what="licw licb masw masb"
fi

for w in $what
do
  docker run chess-exp:latest $w $2 > out/$w.pgn
done
