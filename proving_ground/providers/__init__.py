from proving_ground.providers.base import (
    AccountingProvider,
    Invoice,
    Contact,
    CompanyInfo,
    PaymentStatus,
    ProviderState,
    PurchaseOrder,
    SupplierInvoiceData,
)
from proving_ground.providers.data_generators import (
    generate_invoices,
    generate_contacts,
    generate_support_emails,
    generate_supplier_invoice_data,
    generate_purchase_orders,
    generate_supplier_invoice_emails,
)


def __getattr__(name):
    """Lazy import for MockAccountingProvider to avoid circular import."""
    if name == "MockAccountingProvider":
        from proving_ground.providers.accounting_mock import MockAccountingProvider
        return MockAccountingProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
