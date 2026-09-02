"""
Clinical NLP Pipeline v12
========================
- Endoscopy extractor (procedure + findings)
- Pathology extractor
- Merge engine (endoscopy drives row count, worst-diagnosis-wins)
- Full dropdown normalisation
- Two linked Excel sheets: Procedure + Findings (with pathology merged)
"""

import re
import uuid
import argparse
from difflib import SequenceMatcher
from dataclasses import dataclass, field
from typing import Optional
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from datetime import date
import pandas as pd