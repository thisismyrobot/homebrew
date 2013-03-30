#!/bin/sh

wget --spider http://localhost/api/?house=$1\&unit=$2\&command=$3
