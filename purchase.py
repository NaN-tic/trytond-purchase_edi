# -*- coding: utf-8 -*
# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond.model import fields
from trytond.pool import PoolMeta, Pool
from trytond.pyson import Eval, Bool
import os
from unidecode import unidecode
from trytond.i18n import gettext
from trytond.exceptions import UserError


__all__ = ['Purchase', 'PurchaseConfiguration']


DEFAULT_FILES_LOCATION = '/tmp/'
UOMS = {
    'kg': 'KGM',
    'u': 'PCE',
    'l': 'LTR'
}
CM_TYPES = {
    'phone': 'TE',
    'mobile': 'TE',
    'fax': 'FX',
    'email': 'EM'
}
DATE_FORMAT = '%Y%m%d'


class Purchase(metaclass=PoolMeta):
    __name__ = 'purchase.purchase'

    use_edi = fields.Boolean('Use EDI',
        help='Use EDI protocol for this purchase', states={
                'readonly': ~Bool(Eval('party'))
            }, depends=['party'])
    edi_order_type = fields.Selection([
            ('220', 'Normal Order'),
            ('226', 'Partial order that cancels an open order'),
            ], string='Document Type',
        states={
                'required': Bool(Eval('use_edi')),
                'readonly': ~Bool(Eval('use_edi'))
            }, depends=['use_edi'])
    edi_message_function = fields.Selection([
            ('9', 'Original'),
            ('1', 'Cancellation'),
            ('4', 'Modification'),
            ('5', 'Replacement'),
            ('31', 'Copy'),
            ], string='Message Function',
        states={
                'required': Bool(Eval('use_edi')),
                'readonly': ~Bool(Eval('use_edi'))
            }, depends=['use_edi'])
    edi_special_condition = fields.Selection([
            ('', ''),
            ('81E', 'Bill but not re-supply'),
            ('82E', 'Send but not invoice'),
            ('83E', 'Deliver the entire order'),
            ], string='Special conditions, codified',
        states={
                'readonly': ~Bool(Eval('use_edi'))
            }, depends=['use_edi'])

    supplier_edi_operational_point = fields.Many2One('party.identifier',
        'Supplier EDI Operational Point',
        domain=[
            ('party', '=', Eval('party')),
            ('type', '=', 'edi')
        ],
        states={
            'required': Bool(Eval('use_edi')),
            'readonly': ~Bool(Eval('use_edi') and Eval('party'))
        }, depends=['use_edi', 'party'])

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

    @fields.depends('party')
    def on_change_with_supplier_edi_operational_point(self):
        pool = Pool()
        PartyIdentifier = pool.get('party.identifier')
        if self.party and self.party.allow_edi:
            identifiers = PartyIdentifier.search([
                    ('party', '=', self.party),
                    ('type', '=', 'edi')])
            if len(identifiers) == 1:
                return identifiers[0].id
        return None

    @classmethod
    def confirm(cls, purchases):
        super(Purchase, cls).confirm(purchases)
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
                raise UserError(gettext('unfilled_edi_operational_point',
                        party=party.rec_name))
        customer_invoice_address = self._get_party_address(customer, 'invoice')
        customer_delivery_address = self.warehouse.address if self.warehouse \
            and self.warehouse.address else \
            self._get_party_address(customer, 'delivery')
        if not customer_delivery_address.edi_ean:
            raise UserError(gettext('unfilled_wh_edi_ean',
                    address=customer_delivery_address.id))

        header = 'ORDERS_D_96A_UN_EAN008'
        lines.append(header)
        edi_ord = 'ORD|{0}|{1}|{2}'.format(
            self.number,  # limit 17 chars
            self.edi_order_type,
            self.edi_message_function)
        lines.append(edi_ord.replace('\n', ''))

        edi_dtm = 'DTM|{}'.format(self.purchase_date.strftime(DATE_FORMAT))
        lines.append(edi_dtm.replace('\n', ''))

        if self.edi_special_condition:
            edi_ali = 'ALI|{}'.format(self.edi_special_condition)
            lines.append(edi_ali.replace('\n', ''))

        if self.comment:
            edi_ftx = 'FTX|AAI||{}'.format(self.comment[:280])  # limit 280 chars
            lines.append(edi_ftx.replace('\n', ''))

        edi_nadms = 'NADMS|{0}|{1}|{2}|{3}|{4}|{5}'.format(
                customer.edi_operational_point,
                customer.name[:70],  # limit 70
                customer_invoice_address.street[:70],  # limit 70
                customer_invoice_address.city[:70],  # limit 70
                customer_invoice_address.zip[:10],  # limit 10
                customer.vat_code[:10]  # limit 10
                )
        lines.append(edi_nadms.replace('\n', ''))

        edi_nadmr = 'NADMR|{}'.format(self.supplier_edi_operational_point.code)
        lines.append(edi_nadmr.replace('\n', ''))

        edi_nadsu = 'NADSU|{0}|{1}|{2}|{3}|{4}||{5}'.format(
                self.supplier_edi_operational_point.code,
                supplier.name[:70],  # limit 70
                self.invoice_address.street[:70],  # limit 70
                self.invoice_address.city[:70],  # limit 70
                self.invoice_address.zip[:10],  # limit 10
                supplier.vat_code[:10]  # limit 10
                )
        lines.append(edi_nadsu.replace('\n', ''))

        edi_nadby = 'NADBY|{0}||||{1}|{2}|{3}|{4}|{5}'.format(
            customer.edi_operational_point,
            customer.name[:70],  # limit 70
            customer_invoice_address.street[:70],  # limit 70
            customer_invoice_address.city[:70],  # limit 70
            customer_invoice_address.zip[:10],  # limit 10
            customer.vat_code[:10]  # limit 10
            )
        lines.append(edi_nadby.replace('\n', ''))

        if customer_invoice_address.name:
            edi_ctaby = 'CTABY|OC|{}'.format(
                customer_invoice_address.name[:35])  # limit 35
            lines.append(edi_ctaby.replace('\n', ''))

        edi_naddp = 'NADDP|{0}||{1}|{2}|{3}|{4}'.format(
            customer_delivery_address.edi_ean,
            customer_delivery_address.party.name[:70],  # limit 70
            customer_delivery_address.street[:70],  # limit 70
            customer_delivery_address.city[:70],  # limit 70
            customer_delivery_address.zip[:10],  # limit 10
            )
        lines.append(edi_naddp.replace('\n', ''))

        if customer_delivery_address.name:
            edi_ctadp = 'CTADP|OC|{}'.format(
                customer_delivery_address.name[:35])  # limit 35
            lines.append(edi_ctadp.replace('\n', ''))

        party_cm = customer_delivery_address.party.contact_mechanisms
        for contact_mechanism in party_cm:
            contact_mechanism_type = CM_TYPES.get(contact_mechanism.type, '')
            if contact_mechanism_type:
                edi_comdp = 'COMDP|{0}|{1}'.format(
                        contact_mechanism_type,
                        contact_mechanism.value[:35])  # limit 35
                lines.append(edi_comdp.replace('\n', ''))

        edi_nadiv = 'NADIV|{0}||{1}|{2}|{3}|{4}|{5}'.format(
                customer.edi_operational_point,
                customer.name[:70],  # limit 70
                customer_invoice_address.street[:70],  # limit 70
                customer_invoice_address.city[:70],  # limit 70
                customer_invoice_address.zip[:70],  # limit 10
                customer.vat_code[:10]  # limit 10
                )
        lines.append(edi_nadiv.replace('\n', ''))

        edi_cux = 'CUX|{}'.format(self.currency.code)
        lines.append(edi_cux.replace('\n', ''))

        for index, line in enumerate(self.lines):
            product = line.product
            edi_lin = 'LIN|{0}|EN|{1}'.format(
                product.code_ean13,
                str(index + 1))
            lines.append(edi_lin.replace('\n', ''))
            edi_pialin = 'PIALIN|IN|{}'.format(product.code[:35])  # limit 35
            lines.append(edi_pialin.replace('\n', ''))
            edi_pialin = 'PIALIN|CNA|{}'.format(product.code[:35])  # limit 35
            lines.append(edi_pialin.replace('\n', ''))
            edi_imdlin = 'IMDLIN|F|||{}'.format(product.name[:70])  # limit 70
            lines.append(edi_imdlin.replace('\n', ''))
            edi_qtylin = 'QTYLIN|21|{0}|{1}'.format(
                str(int(line.quantity) or 0),  # limit 15
                UOMS.get(line.unit.symbol, ''))
            lines.append(edi_qtylin.replace('\n', ''))
            if line.delivery_date:
                edi_dtmlin = 'DTMLIN||||{}||'.format(
                    line.delivery_date.strftime(DATE_FORMAT))
                lines.append(edi_dtmlin.replace('\n', ''))
            edi_moalin = 'MOALIN|{}'.format(
                    format(line.amount, '.6f')[:18])
            lines.append(edi_moalin)
            if line.note:
                edi_ftxlin = 'FTXLIN|{}|AAI'.format(line.note[:350])  # limit 350
                lines.append(edi_ftxlin.replace('\n', ''))
            edi_prilin = 'PRILIN|AAA|{0}'.format(
                format(line.unit_price, '.6f')[:18],  # limit 18
                UOMS.get(line.unit.symbol, ''),
                self.currency.code)
            lines.append(edi_prilin.replace('\n', ''))
            edi_prilin = 'PRILIN|AAB|{0}'.format(
                format(line.gross_unit_price, '.6f')[:18],  # limit 18
                UOMS.get(line.unit.symbol, ''),
                self.currency.code)
            lines.append(edi_prilin.replace('\n', ''))
            if line.taxes:
                tax = line.taxes[0].rate * 100
                edi_taxlin = 'TAXLIN|VAT|{}'.format(tax)
                lines.append(edi_taxlin)
            if line.discount:
                discount_value = (
                    line.gross_unit_price - line.unit_price).normalize()
                edi_alclin = 'ALCLIN|A|1|TD||{0}|{1}'.format(
                    str(line.discount*100)[:8],  # limit 8
                    str(discount_value)[:18])  # limit 18
                lines.append(edi_alclin.replace('\n', ''))

        edi_moares = 'MOARES|{}\r\n'.format(str(self.total_amount)[:18])  # limit 18
        lines.append(edi_moares)

        return unidecode('\r\n'.join(lines))

    def _create_edi_order_file(self):
        pool = Pool()
        PurchaseConfig = pool.get('purchase.configuration')
        purchase_config = PurchaseConfig(1)
        path_edi = os.path.abspath(purchase_config.path_edi or
            DEFAULT_FILES_LOCATION)
        if not os.path.isdir(path_edi):
            raise UserError(gettext('path_no_exists',
                    path=path_edi))
        content = self._make_edi_order_content()
        filename = '%s/order_%s.PLA' % (path_edi, self.id)
        with open(filename, 'w') as f:
            f.write(content.encode('utf-8'))


class PurchaseConfiguration(metaclass=PoolMeta):
    __name__ = 'purchase.configuration'

    path_edi = fields.Char('Path EDI')
