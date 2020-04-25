# -*- coding: utf-8 -*
# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond.model import fields, ModelView
from trytond.pool import PoolMeta, Pool
from trytond.pyson import Eval, Bool, Or
import os
from glob import glob
from unidecode import unidecode
from datetime import datetime
from trytond import backend


__all__ = ['Purchase', 'PurchaseConfiguration']


DEFAULT_FILES_LOCATION = '/tmp/'
# Tryton to EDI UOMS mapping
UOMS = {
    'kg': u'KGM',
    'u': u'PCE',
    'l': u'LTR',
    'g': u'GRM',
    'm': u'MTR',
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
        help='Use EDI protocol for this purchase', states={
            'readonly': Or(~Bool(Eval('party')), Bool(Eval('edi_state')))
            }, depends=['party', 'edi_state'])
    edi_order_type = fields.Selection([
            ('220', 'Normal Order'),
            ('226', 'Partial order that cancels an open order'),
            ], 'Document Type', states={
                'required': Bool(Eval('use_edi')),
                'invisible': ~Bool(Eval('use_edi')),
                'readonly': Or(~Bool(Eval('use_edi')),
                    Bool(Eval('state') != 'draft'))
            }, depends=['use_edi', 'edi_state'])
    edi_message_function = fields.Selection([
            ('9', 'Original'),
            ('1', 'Cancellation'),
            ('4', 'Modification'),
            ('5', 'Replacement'),
            ('31', 'Copy'),
            ], 'Message Function', states={
                'invisible': ~Bool(Eval('use_edi')),
                'required': Bool(Eval('use_edi')),
                'readonly': Or(~Bool(Eval('use_edi')),
                    Bool(Eval('state') != 'draft'))
            }, depends=['use_edi', 'edi_state'])
    edi_special_condition = fields.Selection([
            (None, ''),
            ('81E', 'Bill but not re-supply'),
            ('82E', 'Send but not invoice'),
            ('83E', 'Deliver the entire order'),
            ], 'Special conditions, codified', states={
                'invisible': ~Bool(Eval('use_edi')),
                'readonly': Or(~Bool(Eval('use_edi')),
                    Bool(Eval('state') != 'draft'))
            }, depends=['use_edi', 'edi_state'])
    supplier_edi_operational_point = fields.Many2One('party.identifier',
        'Supplier EDI Operational Point',
        domain=[
            ('party', '=', Eval('party')),
            ('type', '=', 'edi')],
        states={
            'invisible': ~Bool(Eval('use_edi')),
            'required': Bool(Eval('use_edi')),
            'readonly': Or(~Bool(Eval('use_edi')), Bool(Eval('edi_state')))
            }, depends=['use_edi', 'edi_state'])
    edi_state = fields.Selection([
        ('pending', 'Pending'),
        ('sended', 'Sended'),
        ], 'EDI Communication State', states={
            'readonly': True,
            'invisible': ~Bool(Eval('use_edi')),
            }, depends=['use_edi'],
        help='State of the EDI communication')
    resend_edi = fields.Selection([
        ('resend', 'Resend'),
        ('no_resend', 'No Resend'),
        ], 'Resend Purchase', states={
            'invisible': ~Bool(Eval('use_edi')),
            }, depends=['use_edi'],
        help='Resend again when purchase is confirmed')

    sended_on = fields.DateTime('Sended On', states={
            'readonly': True,
            'invisible': Eval('edi_state').in_(['pending', None]),
            }, depends=['edi_state'])

    @classmethod
    def __setup__(cls):
        super(Purchase, cls).__setup__()
        cls._error_messages.update({
                'unfilled_edi_operational_point': (
                    'Missing EDI Operational Point from party "%s"'),
                'unfilled_wh_edi_ean': (
                    'Missing EDI EAN Warehouse code from address ID:"%s"'),
                'path_no_exists': ('Path location "%s" doesn\'t exists'),
                'resend_purchase_edi': ('Resend purchase "%(purchase)s"?')
                })
        cls._buttons.update({
            'cancel_purchase_edi': {
                'invisible': (~Eval('state').in_(
                    ['confirmed', 'processing', 'cancel']) |
                        ~Bool(Eval('use_edi')))
            },
        })

    @classmethod
    def view_attributes(cls):
        return super(Purchase, cls).view_attributes() + [
            ('//separator[@id="outgoing_msg"]', 'states', {
                    'invisible': (~Bool(Eval('use_edi'))),
                    })]

    @staticmethod
    def default_resend_edi():
        return 'resend'

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

    @classmethod
    def copy(cls, purchases, default=None):
        if default is None:
            default = {}
        default = default.copy()
        default['use_edi'] = cls.default_use_edi()
        default['edi_order_type'] = cls.default_edi_order_type()
        default['edi_message_function'] = cls.default_edi_message_function()
        default['edi_special_condition'] = cls.default_edi_special_condition()
        default['edi_state'] = None
        default['sended_on'] = None
        return super(Purchase, cls).copy(purchases, default=default)

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
            if purchase.use_edi and purchase.resend_edi == 'resend':
                purchase._create_edi_order_file()
                purchase.edi_state = 'pending'
                purchase.save()



    def _get_party_address(self, party, address_type):
        for address in party.addresses:
            if hasattr(address, address_type) and getattr(
                    address, address_type):
                return address
        return party.addresses[0]

    def __get_edi_cm(self, cm):
        cm_type = CM_TYPES.get(cm.type, '')
        return cm_type, cm.value

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
        if not customer_delivery_address.edi_ean:
            self.raise_user_error('unfilled_wh_edi_ean',
                customer_delivery_address.id)

        header = 'ORDERS_D_96A_UN_EAN008'
        lines.append(header)
        edi_ord = u'ORD|{0}|{1}|{2}'.format(
            self.number,  # limit 17 chars
            self.edi_order_type,
            self.edi_message_function)
        lines.append(edi_ord.replace('\n', '').replace('\r', ''))

        edi_dtm = u'DTM|{}'.format(self.purchase_date.strftime(DATE_FORMAT))
        lines.append(edi_dtm.replace('\n', '').replace('\r', ''))

        edi_rff = u'RFF|VN|{}'.format(self.reference)
        lines.append(edi_rff)

        if self.edi_special_condition:
            edi_ali = u'ALI|{}'.format(self.edi_special_condition)
            lines.append(edi_ali.replace('\n', '').replace('\r', ''))

        if self.comment:
            edi_ftx = u'FTX|AAI||{}'.format(self.comment[:280])  # limit 280 chars
            lines.append(edi_ftx.replace('\n', '').replace('\r', ''))

        edi_nadms = u'NADMS|{0}|{1}|{2}|{3}|{4}|{5}'.format(
                customer.edi_operational_point,
                customer.name[:70],  # limit 70
                customer_invoice_address.street[:70],  # limit 70
                customer_invoice_address.city[:70],  # limit 70
                customer_invoice_address.zip[:10],  # limit 10
                customer.vat_code[:10]  # limit 10
                )
        lines.append(edi_nadms.replace('\n', '').replace('\r', ''))

        edi_nadmr = u'NADMR|{}'.format(self.supplier_edi_operational_point.code)
        lines.append(edi_nadmr.replace('\n', '').replace('\r', ''))

        edi_nadsu = u'NADSU|{0}||{1}|{2}|{3}|{4}|{5}'.format(
                self.supplier_edi_operational_point.code,
                supplier.name[:70],  # limit 70
                self.invoice_address.street[:70],  # limit 70
                self.invoice_address.city[:70],  # limit 70
                self.invoice_address.zip[:10],  # limit 10
                supplier.vat_code[:10]  # limit 10
                )
        lines.append(edi_nadsu.replace('\n', '').replace('\r', ''))

        edi_nadby = u'NADBY|{0}||||{1}|{2}|{3}|{4}|{5}'.format(
            customer.edi_operational_point,
            customer.name[:70],  # limit 70
            customer_invoice_address.street[:70],  # limit 70
            customer_invoice_address.city[:70],  # limit 70
            customer_invoice_address.zip[:10],  # limit 10
            customer.vat_code[:10]  # limit 10
            )
        lines.append(edi_nadby.replace('\n', '').replace('\r', ''))

        if customer_invoice_address.name:
            edi_ctaby = u'CTABY|OC|{}'.format(
                customer_invoice_address.name[:35])  # limit 35
            lines.append(edi_ctaby.replace('\n', '').replace('\r', ''))

        edi_naddp = u'NADDP|{0}||{1}|{2}|{3}|{4}'.format(
            customer_delivery_address.edi_ean,
            customer_delivery_address.party.name[:70],  # limit 70
            customer_delivery_address.street[:70],  # limit 70
            customer_delivery_address.city[:70],  # limit 70
            customer_delivery_address.zip[:10],  # limit 10
            )
        lines.append(edi_naddp.replace('\n', '').replace('\r', ''))

        if customer_delivery_address.name:
            edi_ctadp = u'CTADP|OC|{}'.format(
                customer_delivery_address.name[:35])  # limit 35
            lines.append(edi_ctadp.replace('\n', '').replace('\r', ''))

        party_cm = customer_delivery_address.party.contact_mechanisms
        for contact_mechanism in party_cm:
            cm_type, cm_value = self.__get_edi_cm(contact_mechanism)
            if cm_type:
                edi_comdp = u'COMDP|{0}|{1}'.format(
                        cm_type,
                        cm_value[:35])  # limit 35
                lines.append(edi_comdp.replace('\n', ''))

        edi_nadiv = u'NADIV|{0}||{1}|{2}|{3}|{4}|{5}'.format(
                customer.edi_operational_point,
                customer.name[:70],  # limit 70
                customer_invoice_address.street[:70],  # limit 70
                customer_invoice_address.city[:70],  # limit 70
                customer_invoice_address.zip[:70],  # limit 10
                customer.vat_code[:10]  # limit 10
                )
        lines.append(edi_nadiv.replace('\n', '').replace('\r', ''))

        edi_cux = u'CUX|{}'.format(self.currency.code)
        lines.append(edi_cux.replace('\n', '').replace('\r', ''))

        for index, line in enumerate(self.lines):
            product = line.product
            edi_lin = u'LIN|{0}|EN|{1}'.format(
                product.code_ean13,
                str(index + 1))
            lines.append(edi_lin.replace('\n', '').replace('\r', ''))
            edi_pialin = u'PIALIN|IN|{}'.format(product.code[:35])  # limit 35
            lines.append(edi_pialin.replace('\n', '').replace('\r', ''))
            edi_pialin = u'PIALIN|CNA|{}'.format(product.code[:35])  # limit 35
            lines.append(edi_pialin.replace('\n', '').replace('\r', ''))
            edi_imdlin = u'IMDLIN|F|||{}'.format(product.name[:70])  # limit 70
            lines.append(edi_imdlin.replace('\n', '').replace('\r', ''))
            edi_qtylin = u'QTYLIN|21|{0}|{1}'.format(
                str(int(line.quantity) or 0),  # limit 15
                UOMS.get(line.unit.symbol, ''))
            lines.append(edi_qtylin.replace('\n', '').replace('\r', ''))
            if line.delivery_date:
                edi_dtmlin = u'DTMLIN||||{}||'.format(
                    line.delivery_date.strftime(DATE_FORMAT))
                lines.append(edi_dtmlin.replace('\n', '').replace('\r', ''))
            edi_moalin = u'MOALIN|{}'.format(
                    format(line.amount, '.6f')[:18])
            lines.append(edi_moalin)
            if line.note:
                edi_ftxlin = u'FTXLIN|{}|AAI'.format(line.note[:350])  # limit 350
                lines.append(edi_ftxlin.replace('\n', '').replace('\r', ''))
            edi_prilin = u'PRILIN|AAA|{0}'.format(
                format(line.unit_price, '.6f')[:18],  # limit 18
                UOMS.get(line.unit.symbol, ''),
                self.currency.code)
            lines.append(edi_prilin.replace('\n', '').replace('\r', ''))
            edi_prilin = u'PRILIN|AAB|{0}'.format(
                format(line.gross_unit_price, '.6f')[:18],  # limit 18
                UOMS.get(line.unit.symbol, ''),
                self.currency.code)
            lines.append(edi_prilin.replace('\n', '').replace('\r', ''))
            if line.taxes:
                tax = line.taxes[0].rate * 100
                edi_taxlin = u'TAXLIN|VAT|{}'.format(tax)
                lines.append(edi_taxlin)
            if line.discount:
                discount_value = (
                    line.gross_unit_price - line.unit_price).normalize()
                edi_alclin = u'ALCLIN|A|1|TD||{0}|{1}'.format(
                    str(line.discount * 100)[:8],  # limit 8
                    str(discount_value)[:18])  # limit 18
                lines.append(edi_alclin.replace('\n', '').replace('\r', ''))

        edi_moares = u'MOARES|{}\r\n'.format(str(self.total_amount)[:18])  # limit 18
        lines.append(edi_moares)

        return unidecode('\r\n'.join(lines))

    def _create_edi_order_file(self):
        pool = Pool()
        PurchaseConfig = pool.get('purchase.configuration')
        purchase_config = PurchaseConfig(1)
        path_edi = os.path.abspath(purchase_config.outbox_path_edi or
            DEFAULT_FILES_LOCATION)
        if not os.path.isdir(path_edi):
            self.raise_user_error('path_no_exists', path_edi)
        content = self._make_edi_order_content()
        content = content.encode('utf-8')
        filename = 'order_{}.PLA'.format(self.number)
        with open('{}/{}'.format(path_edi, filename), 'w') as f:
            f.write(content)
        self.add_attachment(content, filename)

    def add_attachment(self, attachment, filename=None):
        pool = Pool()
        Attachment = pool.get('ir.attachment')
        str_date = datetime.now().strftime("%y/%m/%d %H:%M:%S")
        filename = "%(filename)s-%(date)s" % ({'filename':filename or '',
            'date':str_date})
        attach = Attachment(
            name=filename,
            type='data',
            data=attachment,
            resource=str(self))
        attach.save()

    @classmethod
    def update_edi_orders_state(cls):
        pool = Pool()
        Attachment = pool.get('ir.attachment')
        PurchaseConfig = pool.get('purchase.configuration')
        purchase_config = PurchaseConfig(1)
        path_edi = os.path.abspath(purchase_config.outbox_path_edi or
            DEFAULT_FILES_LOCATION)
        old_cwd = os.getcwd()
        os.chdir(path_edi)
        files = glob("order*.*")
        os.chdir(old_cwd)
        pending = set(cls.search([
                ('use_edi', '=', True),
                ('edi_state', '=', 'pending')]))
        attachments = Attachment.search([('name', 'in', files)])
        not_sended = set([a.resource for a in attachments])
        sended = list(pending - not_sended)
        if sended:
            now = datetime.now()
            cls.write(sended, {
                'edi_state': 'sended',
                'sended_on': now,
                })

    @classmethod
    def update_edi_orders_state_cron(cls):
        """
        Cron update edi orders state:
        - State: active
        """
        cls.update_edi_orders_state()
        return True

    @classmethod
    @ModelView.button
    def cancel_purchase_edi(cls, purchases):
        for purchase in purchases:
            purchase.edi_message_function = '1'
            purchase._create_edi_order_file()
            purchase.edi_state = 'pending'
            purchase.save()


class PurchaseConfiguration:
    __metaclass__ = PoolMeta
    __name__ = 'purchase.configuration'

    outbox_path_edi = fields.Char('Outbox Path EDI')

    @classmethod
    def __register__(cls, module_name):
        TableHandler = backend.get('TableHandler')
        table = TableHandler(cls, module_name)

        # Migration from old module versions
        if (not table.column_exist('outbox_path_edi') and
                table.column_exist('path_edi')):
            table.column_rename('path_edi',
                'outbox_path_edi')
        super(PurchaseConfiguration, cls).__register__(module_name)
