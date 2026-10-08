#!/usr/bin/env python3
from common import run
from mpi_common import collective, mpi_parser

def test(c): return collective(c, "reduce")

if __name__ == "__main__":
    raise SystemExit(run("3-2-0009", mpi_parser("3-2-0009", "MPI reduce").parse_args(), test, image="mpi"))
