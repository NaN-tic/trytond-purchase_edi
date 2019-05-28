# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
try:
    from trytond.modules.purchase.tests.test_purchase_edi import suite
except ImportError:
    from .test_purchase_edi import suite

__all__ = ['suite']
