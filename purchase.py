# -*- coding: utf-8 -*
# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond.model import fields
from trytond.pool import PoolMeta, Pool
from trytond.pyson import Eval, Bool
import os
from unidecode import unidecode

__all__ = ['Purchase', 'PurchaseConfiguration']


DEFAULT_FILES_LOCATION = '/tmp/'
UOMS = {
    'kg': u'KGM',
    'u': u'PCE',
    'l': u'LTR'
}
CM_TYPES = {
    'phone': u'TE',
    'mobile': u'TE',
    'fax': u'FX',
    'email': u'EM'
}
DATE_FORMAT = '%Y%m%d'


class Purchase:
    __metaclass__ = PoolMeta
    __name__ = 'purchase.purchase'

    use_edi = fields.Boolean('Use EDI',
        help='Use EDI protocol for this purchase')
    edi_order_type = fields.Selection([
            ('220', 'Normal Order'),
            ('226', 'Partial order that cancels an open order'),
            ], string='Document Type')
    edi_message_function = fields.Selection([
            ('9', 'Original'),
            ('1', 'Cancellation'),
            ('4', 'Modification'),
            ('5', 'Replacement'),
            ('31', 'Copy'),
            ], string='Message Function')
    edi_special_condition = fields.Selection([
            ('', ''),
            ('81E', 'Bill but not re-supply'),
            ('82E', 'Send but not invoice'),
            ('83E', 'Deliver the entire order'),
            ], string='Special conditions, codified')

    @classmethod
    def __setup__(cls):
        super(Purchase, cls).__setup__()
        cls._error_messages.update({
                'unfilled_edi_operational_point': (
                    'Missing EDI Operational Point from party "%s"')
                })

    @staticmethod
    def default_use_edi():
        return False

    @staticmethod
    def default_edi_order_type():
        return '220'

    @staticmethod
    def default_edi_message_function():
        return '9'

    @staticmethod
    def default_edi_special_condition():
        return ''

    @fields.depends('party')
    def on_change_with_use_edi(self):
        if self.party and self.party.allow_edi:
            return True

    @classmethod
    def view_attributes(cls):
        return super(Purchase, cls).view_attributes() + [
            ('//page[@id="edi_page"]', 'states', {
                'invisible': ~Bool(Eval('use_edi', False))
            })]

    @classmethod
    def proceed(cls, purchases):
        super(Purchase, cls).proceed(purchases)
        for purchase in purchases:
            if purchase.use_edi:
                purchase._create_edi_order_file()

    def _get_party_address(self, party, address_type):
        for address in party.addresses:
            if hasattr(address, address_type) and getattr(
                    address, address_type):
                return address
        return party.addresses[0]

    def _make_edi_order_content(self):

        lines = []
        customer = self.company.party
        supplier = self.party
        for party in (customer, supplier):
            if not party.edi_operational_point:
                self.raise_user_error('unfilled_edi_operational_point',
                    party.rec_name)
        customer_invoice_address = self._get_party_address(customer, 'invoice')
        customer_delivery_address = self.warehouse.address if self.warehouse \
            and self.warehouse.address else \
            self._get_party_address(customer, 'delivery')

        header = 'ORDERS_D_96A_UN_EAN008'
        lines.append(header)
        edi_ord = u'ORD|{0}|{1}|{2}'.format(
            self.reference[:17],  # limit 17 chars
            self.edi_order_type,
            self.edi_message_function)
        lines.append(edi_ord)

        edi_dtm = u'DTM|{}'.format(self.purchase_date.strftime(DATE_FORMAT))
        lines.append(edi_dtm)

        if self.edi_special_condition:
            edi_ali = u'ALI|{}'.format(self.edi_special_condition)
            lines.append(edi_ali)

        if self.comment:
            edi_ftx = u'FTX|AAI||{}'.format(self.comment[:280])  # limit 280 chars
            lines.append(edi_ftx)

        edi_nadms = u'NADMS|{0}|{1}|{2}|{3}|{4}|{5}'.format(
                customer.edi_operational_point,
                customer.name[:70],  # limit 70
                customer_invoice_address.street[:70],  # limit 70
                customer_invoice_address.city[:70],  # limit 70
                customer_invoice_address.zip[:10],  # limit 10
                customer.vat_code[:10]  # limit 10
                )
        lines.append(edi_nadms)

        edi_nadmr = u'NADMR|{}'.format(supplier.edi_operational_point)
        lines.append(edi_nadmr)

        edi_nadsu = u'NADSU|{0}|{1}|{2}|{3}|{4}|{5}'.format(
                supplier.edi_operational_point,
                supplier.name[:70],  # limit 70
                self.invoice_address.street[:70],  # limit 70
                self.invoice_address.city[:70],  # limit 70
                self.invoice_address.zip[:10],  # limit 10
                supplier.vat_code[:10]  # limit 10
                )
        lines.append(edi_nadsu)

        edi_nadby = u'NADBY|{0}||||{1}|{2}|{3}|{4}|{5}'.format(
            customer.edi_operational_point,
            customer.name[:70],  # limit 70
            customer_invoice_address.street[:70],  # limit 70
            customer_invoice_address.city[:70],  # limit 70
            customer_invoice_address.zip[:10],  # limit 10
            customer.vat_code[:10]  # limit 10
            )
        lines.append(edi_nadby)

        if customer_invoice_address.name:
            edi_ctaby = u'CTABY|OC|{}'.format(
                customer_invoice_address.name[:35])  # limit 35
            lines.append(edi_ctaby)

        edi_naddp = u'NADDP|{0}||{1}|{2}|{3}|{4}'.format(
            customer.edi_operational_point,
            customer_delivery_address.party.name[:70],  # limit 70
            customer_delivery_address.street[:70],  # limit 70
            customer_delivery_address.city[:70],  # limit 70
            customer_delivery_address.zip[:10],  # limit 10
            )
        lines.append(edi_naddp)

        if customer_delivery_address.name:
            edi_ctadp = u'CTADP|OC|{}'.format(
                customer_delivery_address.name[:35])  # limit 35
            lines.append(edi_ctadp)

        party_cm = customer_delivery_address.party.contact_mechanisms
        for contact_mechanism in party_cm:
            contact_mechanism_type = CM_TYPES.get(contact_mechanism.type, '')
            if contact_mechanism_type:
                edi_comdp = u'COMDP|{0}|{1}'.format(
                        contact_mechanism_type,
                        contact_mechanism.value[:35])  # limit 35
                lines.append(edi_comdp)

        edi_nadiv = u'NADIV|{0}||{1}|{2}|{3}|{4}|{5}'.format(
                customer.edi_operational_point,
                customer.name[:70],  # limit 70
                customer_invoice_address.street[:70],  # limit 70
                customer_invoice_address.city[:70],  # limit 70
                customer_invoice_address.zip[:70],  # limit 10
                customer.vat_code[:10]  # limit 10
                )
        lines.append(edi_nadiv)

        edi_cux = u'CUX|{}'.format(self.currency.code)
        lines.append(edi_cux)

        for index, line in enumerate(self.lines):
            product = line.product
            edi_lin = u'LIN|{0}|EN|{1}'.format(
                product.code_ean13,
                str(index + 1))
            lines.append(edi_lin)
            edi_pialin = u'PIALIN|IN|{}'.format(product.code[:35])  # limit 35
            lines.append(edi_pialin)
            edi_imdlin = u'IMDLIN|F|||{}'.format(product.name[:70])  # limit 70
            lines.append(edi_imdlin)
            edi_qtylin = u'QTYLIN|21|{0}|{1}'.format(
                str(int(line.quantity) or 0),  # limit 15
                UOMS.get(line.unit.symbol, ''))
            lines.append(edi_qtylin)
            if line.delivery_date:
                edi_dtmlin = u'DTMLIN||||{}||'.format(
                    line.delivery_date.strftime(DATE_FORMAT))
                lines.append(edi_dtmlin)
            if line.note:
                edi_ftxlin = u'FTXLIN|{}|AAI'.format(line.note[:350])  # limit 350
                lines.append(edi_ftxlin)
            edi_prilin = u'PRILIN|AAA|{0}|||{1}|{2}'.format(
                format(line.unit_price, '.6f')[:18],  # limit 18
                UOMS.get(line.unit.symbol, ''),
                self.currency.code)
            lines.append(edi_prilin)
            edi_prilin = u'PRILIN|AAB|{0}|||{1}|{2}'.format(
                format(line.gross_unit_price, '.6f')[:18],  # limit 18
                UOMS.get(line.unit.symbol, ''),
                self.currency.code)
            lines.append(edi_prilin)
            if line.discount:
                discount_value = (
                    line.gross_unit_price - line.unit_price).normalize()
                edi_alclin = u'ALCLIN|A|1|TD|{0}|{1}'.format(
                    str(line.discount)[:8],  # limit 8
                    str(discount_value)[:18])  # limit 18
                lines.append(edi_alclin)

        edi_moares = u'MOARES|{}\r\n'.format(str(self.total_amount)[:18])  # limit 18
        lines.append(edi_moares)

        return unidecode('\r\n'.join(lines))

    def _create_edi_order_file(self):
        pool = Pool()
        PurchaseConfig = pool.get('purchase.configuration')
        purchase_config = PurchaseConfig(1)
        path_edi = os.path.abspath(purchase_config.path_edi or
            DEFAULT_FILES_LOCATION)
        content = self._make_edi_order_content()
        filename = '%s/order_%s.PLA' % (path_edi, self.id)
        with open(filename, 'w') as f:
            f.write(content.encode('utf-8'))


class PurchaseConfiguration:
    __metaclass__ = PoolMeta
    __name__ = 'purchase.configuration'

    path_edi = fields.Char('Path EDI')
