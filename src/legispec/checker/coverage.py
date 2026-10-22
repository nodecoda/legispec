# ===----------------------------------------------------------------------===##
#
# Part of the LegiSpec project, under the Apache License v2.0 with LLVM
# Exceptions.  See LICENSE.TXT for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
#
# ===----------------------------------------------------------------------===##

"""Compatibility wrapper for the top-level coverage module."""

from ..coverage import CoverageEntry, CoverageReport, run_coverage

__all__ = ["CoverageEntry", "CoverageReport", "run_coverage"]
