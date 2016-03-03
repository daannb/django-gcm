import logging

from django.db import models
from django.db.models.query import QuerySet
from django.utils.encoding import python_2_unicode_compatible

from django.utils.translation import ugettext_lazy as _

from . import conf
from . import api
from .utils import load_object
import json


logger = logging.getLogger(__name__)


def get_device_model():
    return load_object(conf.GCM_DEVICE_MODEL)


class GCMMessage(models.Model, api.GCMMessage):

    SENT = "SENT"
    PENDING = "PENDING"
    FAILED = "FAILED"
    NONE = "NONE"

    STATUS_CHOICES = (
        (SENT, "Sent"),
        (PENDING, "Pending"),
        (FAILED, "Failed"),
        (NONE, "None"))

    topic = models.CharField(
        verbose_name=_("Topic"), max_length=50, blank=True)
    created = models.DateTimeField(
        verbose_name=_("Created"), auto_now_add=True)
    message = models.TextField(
        verbose_name=_("Message"))
    response = models.CharField(
        verbose_name=_("Response"), max_length=255)
    status = models.CharField(
        verbose_name=_("Status"), max_length=16, choices=STATUS_CHOICES, default=NONE)
    devices = models.ManyToManyField('gcm.Device')
    #priority = models.CharField(verbose_name=_("Priority"), max_length=255)
    #content_available = models.CharField(verbose_name=_("Available Content"), max_length=255)

    GCM_INVALID_ID_ERRORS = ['InvalidRegistration',
                             'NotRegistered',
                             'MismatchSenderId']

    def send(self, data, registration_ids=None, topic=None, **kwargs):
        self.status = self.PENDING
        self.message = json.dumps(data)
        self.topic = ""
        if topic:
            self.topic = topic
            kwargs['to'] = "/topics/{topic}".format(topic=topic)
        self.save()
        response = super(GCMMessage, self).send(
            data, registration_ids=registration_ids, **kwargs)
        chunks = [response] if not isinstance(response, list) else response
        for chunk in chunks:
            self.post_send(*chunk)

        self.save()
        return response

    def post_send(self, registration_ids, response):
        if 'error' in response or 'failure' in response:
            self.status = self.FAILED
            invalid_messages = dict(filter(
                lambda x: x[1].get('error') in self.GCM_INVALID_ID_ERRORS,
                zip(registration_ids, response.get('results'))))

            regs = list(invalid_messages.keys())
            for device in get_device_model().objects.filter(reg_id__in=regs):
                device.mark_inactive(
                    error_message=invalid_messages[device.reg_id]['error'])
        else:
            self.status = self.SENT


    def __str__(self):
        return self.message

class DeviceQuerySet(QuerySet):

    def send_message(self, data, **kwargs):
        if self:
            registration_ids = list(self.values_list("reg_id", flat=True))
            return GCMMessage().send(
                data, registration_ids=registration_ids, **kwargs)


class DeviceManager(models.Manager):

    def get_queryset(self):
        return DeviceQuerySet(self.model)
    get_query_set = get_queryset  # Django < 1.6 compatiblity


@python_2_unicode_compatible
class AbstractDevice(models.Model):

    dev_id = models.CharField(
        verbose_name=_("Device ID"), max_length=50, unique=True,)
    reg_id = models.CharField(
        verbose_name=_("Registration ID"), max_length=255, unique=True)
    name = models.CharField(
        verbose_name=_("Name"), max_length=255, blank=True, null=True)
    creation_date = models.DateTimeField(
        verbose_name=_("Creation date"), auto_now_add=True)
    modified_date = models.DateTimeField(
        verbose_name=_("Modified date"), auto_now=True)
    is_active = models.BooleanField(
        verbose_name=_("Is active?"), default=False)

    objects = DeviceManager()

    def __str__(self):
        return self.dev_id

    class Meta:
        abstract = True
        verbose_name = _("Device")
        verbose_name_plural = _("Devices")
        ordering = ['-modified_date']

    def send_message(self, data, **kwargs):
        return GCMMessage().send(
            registration_ids=[self.reg_id], data=data, **kwargs)

    def mark_inactive(self, **kwargs):
        self.is_active = False
        self.save()
        if kwargs.get('error_message'):
            logger.debug("Device %s (%s) marked inactive due to error: %s",
                         self.dev_id, self.name, kwargs['error_message'])


class Device(AbstractDevice):
    pass
