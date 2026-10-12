import { useEffect, useState } from "react";
import { Button, Card, DatePicker, Form, Radio, Select, Space, Table, TimePicker, Typography, Upload, message } from "antd";
import { InboxOutlined } from "@ant-design/icons";
import type { UploadFile } from "antd/es/upload/interface";
import dayjs from "dayjs";
import { Batch, FILE_TYPE_LABEL, FileType, RangeType, errorMessage, listBatches, uploadFile } from "../../api/uploads";

interface FormValues {
  file_type: FileType;
  range_type?: RangeType;
  biz_date?: dayjs.Dayjs;
  period?: [dayjs.Dayjs, dayjs.Dayjs];
}

export default function UploadPage() {
  const [form] = Form.useForm<FormValues>();
  const fileType = Form.useWatch("file_type", form);
  const rangeType = Form.useWatch("range_type", form);
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [batches, setBatches] = useState<Batch[]>([]);

  const needsWindow = fileType === "query" || fileType === "quality";

  const reload = () => listBatches().then(setBatches).catch(() => setBatches([]));
  useEffect(() => {
    reload();
  }, []);

  const onFinish = async (values: FormValues) => {
    const raw = fileList[0]?.originFileObj;
    if (!raw) {
      message.warning("请选择要上传的 Excel 文件");
      return;
    }
    setSubmitting(true);
    try {
      const fields =
        values.file_type === "order"
          ? { file_type: "order" as const }
          : {
              file_type: values.file_type,
              range_type: values.range_type,
              biz_date: values.biz_date?.format("YYYY-MM-DD"),
              period_start: values.range_type === "period" ? values.period?.[0].format("HH:mm") : undefined,
              period_end: values.range_type === "period" ? values.period?.[1].format("HH:mm") : undefined,
            };
      const batch = await uploadFile(raw as File, fields);
      message.success(`${FILE_TYPE_LABEL[batch.file_type]}导入成功，共 ${batch.row_count} 条`);
      setFileList([]);
      reload();
    } catch (err) {
      message.error(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Card title="数据上传">
        <Form form={form} layout="vertical" initialValues={{ file_type: "order", range_type: "full_day" }} onFinish={onFinish}>
          <Form.Item name="file_type" label="文件类型" rules={[{ required: true }]}>
            <Select
              style={{ maxWidth: 320 }}
              options={[
                { value: "order", label: "订单表（OrderList，时间由表内支付时间决定）" },
                { value: "query", label: "查询量表" },
                { value: "quality", label: "数据质量表" },
              ]}
            />
          </Form.Item>

          {needsWindow && (
            <>
              <Form.Item name="range_type" label="数据范围" rules={[{ required: true }]}>
                <Radio.Group>
                  <Radio value="full_day">完整一天</Radio>
                  <Radio value="period">某个时间段</Radio>
                </Radio.Group>
              </Form.Item>
              <Form.Item name="biz_date" label="业务日期" rules={[{ required: true, message: "请选择业务日期" }]}>
                <DatePicker />
              </Form.Item>
              {rangeType === "period" && (
                <Form.Item name="period" label="时间段（北京时间）" rules={[{ required: true, message: "请选择时间段" }]}>
                  <TimePicker.RangePicker format="HH:mm" />
                </Form.Item>
              )}
            </>
          )}

          <Form.Item label="Excel 文件" required>
            <Upload.Dragger
              accept=".xlsx"
              maxCount={1}
              fileList={fileList}
              beforeUpload={() => false}
              onChange={({ fileList: next }) => setFileList(next.slice(-1))}
            >
              <p className="ant-upload-drag-icon">
                <InboxOutlined />
              </p>
              <p className="ant-upload-text">点击或拖拽 .xlsx 文件到此处</p>
              <p className="ant-upload-hint">原始文件不会保存，只保存解析后的数据（乘机人姓名等敏感字段不入库）</p>
            </Upload.Dragger>
          </Form.Item>

          <Button type="primary" htmlType="submit" loading={submitting}>
            开始导入
          </Button>
        </Form>
      </Card>

      <Card title="导入记录">
        <Table<Batch>
          rowKey="id"
          size="small"
          dataSource={batches}
          pagination={{ pageSize: 10 }}
          columns={[
            { title: "批次", dataIndex: "id", width: 70 },
            { title: "类型", dataIndex: "file_type", width: 110, render: (t: FileType) => FILE_TYPE_LABEL[t] },
            { title: "文件名", dataIndex: "file_name", ellipsis: true },
            {
              title: "数据范围",
              width: 220,
              render: (_: unknown, b: Batch) =>
                b.file_type === "order"
                  ? `${b.data_min_time ?? "-"} ~ ${b.data_max_time ?? "-"}`
                  : `${b.biz_date} ${b.range_type === "full_day" ? "全天" : `${b.period_start}-${b.period_end}`}`,
            },
            { title: "记录数", dataIndex: "row_count", width: 90 },
            { title: "上传时间", dataIndex: "uploaded_at", width: 180 },
          ]}
        />
        <Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>
          同一文件（内容相同）不会重复导入。查询量、数据质量表同一日期与范围的记录会被覆盖更新。
        </Typography.Paragraph>
      </Card>
    </Space>
  );
}
