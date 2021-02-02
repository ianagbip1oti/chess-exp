#!/bin/bash

docker build . -t chess-exp:latest

mkdir -p out

what=$@

if [[ -z "$what" ]]
then
  what="winw winb losew loseb"
fi

for w in $what
do
  docker run chess-exp:latest $w > out/$w.pgn
done
