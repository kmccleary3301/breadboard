from __future__ import annotations
from pathlib import Path
from typing import Any
from breadboard.product.operations.model import OperationResult,EXIT_BLOCKED,from_exception
def _catalog():
    from breadboard.product.integrations import IntegrationCatalog
    return IntegrationCatalog()
def _record(x):
    if hasattr(x,"to_record"):return x.to_record()
    if hasattr(x,"descriptor"):return _record(x.descriptor)
    if isinstance(x,(list,tuple)):return [_record(v) for v in x]
    if isinstance(x,dict):return {str(k):_record(v) for k,v in x.items()}
    return x
def list_integrations(args):
    try:
        rows=_record(_catalog().list()); rows=rows if isinstance(rows,list) else [rows]; return OperationResult.success(["integration","list"],{"integrations":rows,"count":len(rows)},stage="integration.list")
    except ModuleNotFoundError:return OperationResult.failure(["integration","list"],EXIT_BLOCKED,"integration_catalog_unavailable","integration catalog is unavailable in this installation","integration.list",status="blocked")
    except Exception as e:return from_exception(["integration","list"],e,"integration.list")
def get(args):
    try:
        x=_record(_catalog().get(args.INTEGRATION_ID)); return OperationResult.failure(["integration","get"],EXIT_BLOCKED,"integration_not_found",f"integration not found: {args.INTEGRATION_ID}","integration.get",next_actions=["breadboard integration list"],status="blocked") if x is None else OperationResult.success(["integration","get"],{"integration":x},stage="integration.get")
    except KeyError:return OperationResult.failure(["integration","get"],EXIT_BLOCKED,"integration_not_found",f"integration not found: {args.INTEGRATION_ID}","integration.get",next_actions=["breadboard integration list"],status="blocked")
    except ModuleNotFoundError:return OperationResult.failure(["integration","get"],EXIT_BLOCKED,"integration_catalog_unavailable","integration catalog is unavailable in this installation","integration.get",status="blocked")
    except Exception as e:return from_exception(["integration","get"],e,"integration.get")
def probe(args):
    try:return OperationResult.success(["integration","probe"],{"probe":_record(_catalog().probe(getattr(args,"INTEGRATION_ID",None)))},stage="integration.probe")
    except ModuleNotFoundError:return OperationResult.failure(["integration","probe"],EXIT_BLOCKED,"integration_catalog_unavailable","integration catalog is unavailable in this installation","integration.probe",status="blocked")
    except Exception as e:return from_exception(["integration","probe"],e,"integration.probe")
